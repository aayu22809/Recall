//! Google OAuth 2.0 — installed-app PKCE flow with loopback redirect.
//!
//! Flow (driven by `start_flow`):
//!   1. Bind a loopback TCP listener on a free high port.
//!   2. Build the auth URL with PKCE S256, a 32-byte random `state`, and the
//!      union of scopes the caller toggled on. Open it in the user's default
//!      browser via `tauri-plugin-opener`.
//!   3. Wait for Google to redirect back to `http://127.0.0.1:<port>?code=…`.
//!      Validate `state`, exchange `code` + verifier for tokens.
//!   4. Return a `GoogleTokens` struct that the caller formats into the
//!      Python-compatible JSON for `~/.vef/credentials/<source>.json`.
//!
//! For v0.4.0 the bundled `client_id` + `client_secret` come from a Google
//! "Testing"-mode OAuth client. Google treats installed-app secrets as public
//! identifiers, not secrets — distributing them in the signed binary is per
//! their installed-app guidance.

use std::collections::HashMap;
use std::time::{Duration, SystemTime, UNIX_EPOCH};

use base64::Engine;
use rand::RngCore;
use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};
use tokio::io::{AsyncReadExt, AsyncWriteExt};
use tokio::net::TcpListener;
use url::Url;

const AUTH_ENDPOINT: &str = "https://accounts.google.com/o/oauth2/v2/auth";
const TOKEN_ENDPOINT: &str = "https://oauth2.googleapis.com/token";

// Replace these with the values from the Recall Google Cloud project before
// shipping a build to testers. They live in source so the build is reproducible
// without any build-time secret-fetch step.
//
// CLIENT_ID is harmless to publish (Google's "installed app" model treats it
// as a public identifier). CLIENT_SECRET is also published per Google's docs:
// https://developers.google.com/identity/protocols/oauth2/native-app#step1-get-client-credentials
const CLIENT_ID: Option<&str> = option_env!("RECALL_GOOGLE_CLIENT_ID");
const CLIENT_SECRET: Option<&str> = option_env!("RECALL_GOOGLE_CLIENT_SECRET");

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct GoogleTokens {
    pub access_token: String,
    pub refresh_token: Option<String>,
    pub token_uri: String,
    pub client_id: String,
    pub client_secret: String,
    pub scopes: Vec<String>,
    pub expiry_unix_s: u64,
}

impl GoogleTokens {
    /// Convert to the JSON shape `Credentials.from_authorized_user_info`
    /// accepts.
    pub fn to_credentials_json(&self) -> serde_json::Value {
        serde_json::json!({
            "token": self.access_token,
            "refresh_token": self.refresh_token,
            "token_uri": self.token_uri,
            "client_id": self.client_id,
            "client_secret": self.client_secret,
            "scopes": self.scopes,
            "expiry": chrono_iso(self.expiry_unix_s),
        })
    }
}

fn chrono_iso(unix_s: u64) -> String {
    // ISO 8601 in UTC. We avoid pulling the `chrono` crate; this is enough.
    let secs = unix_s as i64;
    let days = secs.div_euclid(86_400);
    let rem = secs.rem_euclid(86_400);
    let h = rem / 3600;
    let m = (rem / 60) % 60;
    let s = rem % 60;
    let (y, mo, d) = days_to_ymd(days);
    format!("{y:04}-{mo:02}-{d:02}T{h:02}:{m:02}:{s:02}Z")
}

fn days_to_ymd(days_since_epoch: i64) -> (i64, u32, u32) {
    // 1970-01-01 epoch → Y/M/D, sufficient for token expiries.
    let mut z = days_since_epoch + 719_468;
    let era = z.div_euclid(146_097);
    let doe = z - era * 146_097;
    let yoe = (doe - doe / 1_460 + doe / 36_524 - doe / 146_096) / 365;
    let y = yoe + era * 400;
    let doy = doe - (365 * yoe + yoe / 4 - yoe / 100);
    let mp = (5 * doy + 2) / 153;
    let d = (doy - (153 * mp + 2) / 5 + 1) as u32;
    let m = if mp < 10 { mp + 3 } else { mp - 9 } as u32;
    z = if m <= 2 { y + 1 } else { y };
    (z, m, d)
}

#[derive(Debug, thiserror::Error)]
pub enum OAuthError {
    #[error("listener bind failed: {0}")]
    Bind(String),
    #[error("transport: {0}")]
    Transport(String),
    #[error("state mismatch — possible CSRF")]
    StateMismatch,
    #[error("user denied or returned no code")]
    NoCode,
    #[error("token exchange failed: {0}")]
    Exchange(String),
    #[error("timed out waiting for callback")]
    Timeout,
    #[error("Google OAuth is not configured for this build")]
    MissingConfig,
}

pub async fn start_flow(
    scopes: &[&str],
    open_url: impl FnOnce(&str),
) -> Result<GoogleTokens, OAuthError> {
    let client_id = CLIENT_ID
        .filter(|v| !v.is_empty())
        .ok_or(OAuthError::MissingConfig)?;
    let client_secret = CLIENT_SECRET
        .filter(|v| !v.is_empty())
        .ok_or(OAuthError::MissingConfig)?;

    let listener = TcpListener::bind("127.0.0.1:0")
        .await
        .map_err(|e| OAuthError::Bind(e.to_string()))?;
    let port = listener
        .local_addr()
        .map_err(|e| OAuthError::Bind(e.to_string()))?
        .port();
    let redirect_uri = format!("http://127.0.0.1:{port}");

    let (verifier, challenge) = make_pkce();
    let state = random_state();
    let auth_url = build_auth_url(client_id, &redirect_uri, &challenge, &state, scopes);
    open_url(&auth_url);

    let (code, returned_state) =
        match tokio::time::timeout(Duration::from_secs(300), await_callback(listener)).await {
            Ok(Ok(v)) => v,
            Ok(Err(e)) => return Err(e),
            Err(_) => return Err(OAuthError::Timeout),
        };

    if returned_state != state {
        return Err(OAuthError::StateMismatch);
    }

    let tokens = exchange_code(
        client_id,
        client_secret,
        &code,
        &verifier,
        &redirect_uri,
        scopes,
    )
    .await?;
    Ok(tokens)
}

fn random_state() -> String {
    let mut buf = [0u8; 24];
    rand::thread_rng().fill_bytes(&mut buf);
    base64::engine::general_purpose::URL_SAFE_NO_PAD.encode(buf)
}

fn make_pkce() -> (String, String) {
    let mut buf = [0u8; 32];
    rand::thread_rng().fill_bytes(&mut buf);
    let verifier = base64::engine::general_purpose::URL_SAFE_NO_PAD.encode(buf);
    let mut hasher = Sha256::new();
    hasher.update(verifier.as_bytes());
    let challenge = base64::engine::general_purpose::URL_SAFE_NO_PAD.encode(hasher.finalize());
    (verifier, challenge)
}

fn build_auth_url(
    client_id: &str,
    redirect_uri: &str,
    challenge: &str,
    state: &str,
    scopes: &[&str],
) -> String {
    let mut url = Url::parse(AUTH_ENDPOINT).expect("static auth endpoint");
    url.query_pairs_mut()
        .append_pair("client_id", client_id)
        .append_pair("redirect_uri", redirect_uri)
        .append_pair("response_type", "code")
        .append_pair("scope", &scopes.join(" "))
        .append_pair("state", state)
        .append_pair("code_challenge", challenge)
        .append_pair("code_challenge_method", "S256")
        .append_pair("access_type", "offline")
        .append_pair("prompt", "consent");
    url.to_string()
}

async fn await_callback(listener: TcpListener) -> Result<(String, String), OAuthError> {
    let (mut sock, _peer) = listener
        .accept()
        .await
        .map_err(|e| OAuthError::Transport(e.to_string()))?;
    let mut buf = [0u8; 4096];
    let n = sock
        .read(&mut buf)
        .await
        .map_err(|e| OAuthError::Transport(e.to_string()))?;
    let req = String::from_utf8_lossy(&buf[..n]);
    let path = req
        .lines()
        .next()
        .and_then(|l| l.split_whitespace().nth(1))
        .ok_or(OAuthError::NoCode)?;
    let parsed = Url::parse(&format!("http://127.0.0.1{path}")).map_err(|_| OAuthError::NoCode)?;

    let mut params: HashMap<String, String> = HashMap::new();
    for (k, v) in parsed.query_pairs() {
        params.insert(k.into_owned(), v.into_owned());
    }
    let code = params.remove("code").ok_or(OAuthError::NoCode)?;
    let state = params.remove("state").unwrap_or_default();

    // Best-effort success page.
    let body = include_str!("../../resources/oauth_success.html");
    let resp = format!(
        "HTTP/1.1 200 OK\r\nContent-Type: text/html; charset=utf-8\r\nContent-Length: {}\r\nConnection: close\r\n\r\n{}",
        body.len(),
        body
    );
    let _ = sock.write_all(resp.as_bytes()).await;
    let _ = sock.shutdown().await;
    Ok((code, state))
}

async fn exchange_code(
    client_id: &str,
    client_secret: &str,
    code: &str,
    verifier: &str,
    redirect_uri: &str,
    scopes: &[&str],
) -> Result<GoogleTokens, OAuthError> {
    let body = [
        ("code", code),
        ("client_id", client_id),
        ("client_secret", client_secret),
        ("code_verifier", verifier),
        ("grant_type", "authorization_code"),
        ("redirect_uri", redirect_uri),
    ];
    let resp = reqwest::Client::new()
        .post(TOKEN_ENDPOINT)
        .form(&body)
        .send()
        .await
        .map_err(|e| OAuthError::Exchange(e.to_string()))?;
    if !resp.status().is_success() {
        let txt = resp.text().await.unwrap_or_default();
        return Err(OAuthError::Exchange(txt));
    }
    let parsed: TokenResponse = resp
        .json()
        .await
        .map_err(|e| OAuthError::Exchange(e.to_string()))?;
    let now = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|d| d.as_secs())
        .unwrap_or(0);
    Ok(GoogleTokens {
        access_token: parsed.access_token,
        refresh_token: parsed.refresh_token,
        token_uri: TOKEN_ENDPOINT.to_string(),
        client_id: client_id.to_string(),
        client_secret: client_secret.to_string(),
        scopes: scopes.iter().map(|s| s.to_string()).collect(),
        expiry_unix_s: now + parsed.expires_in.unwrap_or(3600),
    })
}

#[derive(Deserialize)]
struct TokenResponse {
    access_token: String,
    refresh_token: Option<String>,
    expires_in: Option<u64>,
    #[allow(dead_code)]
    token_type: Option<String>,
    #[allow(dead_code)]
    scope: Option<String>,
}
