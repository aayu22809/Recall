use std::path::PathBuf;
use std::time::Duration;

use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
use tauri::{AppHandle, Manager, State};
use tauri_plugin_opener::OpenerExt;
use tauri_plugin_store::StoreExt;

use crate::daemon_client::{DaemonClient, IndexStatus, Progress, SearchResult, Stats, WatchedDirs};
use crate::keychain;
use crate::local_config::{self, ConfigApplyResult};
use crate::oauth;
use crate::sidecar;

const STORE_FILE: &str = "recall.app.store.json";
const DAEMON_TIMEOUT: Duration = Duration::from_secs(20);

fn err<E: ToString>(error: E) -> String {
    error.to_string()
}

async fn ensure_daemon(app: &AppHandle) -> Result<sidecar::StatusPayload, String> {
    sidecar::ensure_running(app, DAEMON_TIMEOUT).await
}

#[tauri::command]
pub fn daemon_status(app: AppHandle) -> Result<sidecar::StatusPayload, String> {
    Ok(app.state::<sidecar::DaemonController>().snapshot())
}

#[tauri::command]
pub async fn daemon_ensure_running(app: AppHandle) -> Result<sidecar::StatusPayload, String> {
    ensure_daemon(&app).await
}

#[tauri::command]
pub async fn daemon_restart(app: AppHandle) -> Result<sidecar::StatusPayload, String> {
    sidecar::restart(&app, DAEMON_TIMEOUT).await
}

#[derive(Debug, Deserialize)]
pub struct SearchArgs {
    pub query: String,
    #[serde(default = "default_n")]
    pub n_results: usize,
    pub sources: Option<Vec<String>>,
}

fn default_n() -> usize {
    20
}

#[tauri::command]
pub async fn search(
    args: SearchArgs,
    client: State<'_, DaemonClient>,
    app: AppHandle,
) -> Result<Vec<SearchResult>, String> {
    ensure_daemon(&app).await?;
    client
        .search(&args.query, args.n_results, args.sources)
        .await
        .map_err(err)
}

#[tauri::command]
pub async fn stats(client: State<'_, DaemonClient>, app: AppHandle) -> Result<Stats, String> {
    ensure_daemon(&app).await?;
    client.stats().await.map_err(err)
}

#[tauri::command]
pub async fn sources(client: State<'_, DaemonClient>, app: AppHandle) -> Result<Vec<String>, String> {
    ensure_daemon(&app).await?;
    Ok(client.sources().await.map_err(err)?.sources)
}

#[tauri::command]
pub async fn progress(client: State<'_, DaemonClient>, app: AppHandle) -> Result<Progress, String> {
    ensure_daemon(&app).await?;
    client.progress().await.map_err(err)
}

#[tauri::command]
pub async fn index_status(
    client: State<'_, DaemonClient>,
    app: AppHandle,
) -> Result<IndexStatus, String> {
    ensure_daemon(&app).await?;
    client.index_status().await.map_err(err)
}

#[tauri::command]
pub async fn connector_status(client: State<'_, DaemonClient>, app: AppHandle) -> Result<Value, String> {
    ensure_daemon(&app).await?;
    client.connector_status().await.map_err(err)
}

#[tauri::command]
pub async fn sync(
    source: Option<String>,
    client: State<'_, DaemonClient>,
    app: AppHandle,
) -> Result<Value, String> {
    ensure_daemon(&app).await?;
    if let Some(source) = source {
        return client.sync(Some(source.as_str())).await.map_err(err);
    }

    let files = client.index_watched_dirs().await.map_err(err)?;
    let sources = client.sync(None).await.map_err(err)?;
    Ok(json!({
        "status": "started",
        "files": files,
        "sources": sources,
    }))
}

#[tauri::command]
pub async fn sync_running(client: State<'_, DaemonClient>, app: AppHandle) -> Result<bool, String> {
    ensure_daemon(&app).await?;
    client.sync_running().await.map_err(err)
}

#[tauri::command]
pub async fn watched_dirs(client: State<'_, DaemonClient>, app: AppHandle) -> Result<WatchedDirs, String> {
    ensure_daemon(&app).await?;
    client.watched_dirs().await.map_err(err)
}

#[tauri::command]
pub async fn watched_dirs_stats(client: State<'_, DaemonClient>, app: AppHandle) -> Result<Value, String> {
    ensure_daemon(&app).await?;
    client.watched_dirs_stats().await.map_err(err)
}

#[tauri::command]
pub async fn add_watched_dir(
    path: String,
    client: State<'_, DaemonClient>,
    app: AppHandle,
) -> Result<WatchedDirs, String> {
    ensure_daemon(&app).await?;
    client.add_watched_dir(&path).await.map_err(err)
}

#[tauri::command]
pub async fn remove_watched_dir(
    path: String,
    client: State<'_, DaemonClient>,
    app: AppHandle,
) -> Result<WatchedDirs, String> {
    ensure_daemon(&app).await?;
    client.remove_watched_dir(&path).await.map_err(err)
}

#[tauri::command]
pub async fn index_watched_dirs(client: State<'_, DaemonClient>, app: AppHandle) -> Result<Value, String> {
    ensure_daemon(&app).await?;
    client.index_watched_dirs().await.map_err(err)
}

#[tauri::command]
pub async fn ingest(
    path: String,
    source: String,
    description: Option<String>,
    client: State<'_, DaemonClient>,
    app: AppHandle,
) -> Result<Value, String> {
    ensure_daemon(&app).await?;
    client
        .ingest(&path, &source, description.as_deref().unwrap_or(""))
        .await
        .map_err(err)
}

#[tauri::command]
pub async fn configure(payload: Value, app: AppHandle) -> Result<ConfigApplyResult, String> {
    let changed = local_config::apply_config_updates(&payload)?;
    let should_restart = local_config::config_change_requires_restart(&changed);

    if should_restart {
        sidecar::restart(&app, DAEMON_TIMEOUT).await?;
    }

    Ok(ConfigApplyResult {
        ok: true,
        restarted: should_restart,
        env_path: local_config::env_path()?.to_string_lossy().to_string(),
    })
}

#[derive(Debug, Deserialize, Serialize)]
pub struct OllamaInfo {
    pub installed: bool,
    pub models: Vec<String>,
}

#[tauri::command]
pub async fn ollama_detect() -> Result<OllamaInfo, String> {
    let response = reqwest::Client::builder()
        .timeout(Duration::from_millis(800))
        .build()
        .map_err(err)?
        .get("http://127.0.0.1:11434/api/tags")
        .send()
        .await;

    let info = match response {
        Ok(response) if response.status().is_success() => {
            let payload: Value = response.json().await.unwrap_or_default();
            let models = payload
                .get("models")
                .and_then(|value| value.as_array())
                .map(|items| {
                    items
                        .iter()
                        .filter_map(|item| {
                            item.get("name")
                                .and_then(|name| name.as_str())
                                .map(String::from)
                        })
                        .collect()
                })
                .unwrap_or_default();
            OllamaInfo {
                installed: true,
                models,
            }
        }
        _ => OllamaInfo {
            installed: false,
            models: vec![],
        },
    };

    Ok(info)
}

#[derive(Debug, Deserialize)]
pub struct ConnectGoogleArgs {
    pub source: String,
    pub scopes: Vec<String>,
}

#[derive(Debug, Serialize)]
pub struct ConnectGoogleResult {
    pub source: String,
    pub credentials_path: String,
}

#[tauri::command]
pub async fn oauth_connect_google(
    args: ConnectGoogleArgs,
    app: AppHandle,
) -> Result<ConnectGoogleResult, String> {
    let scope_refs = combined_google_scopes(args.scopes);
    let app_for_open = app.clone();
    let tokens = oauth::google::start_flow(&scope_refs, move |url| {
        let _ = app_for_open.opener().open_url(url, None::<&str>);
    })
    .await
    .map_err(err)?;

    if let Some(refresh_token) = tokens.refresh_token.as_ref() {
        let _ = keychain::set("oauth.google.shared.refresh", refresh_token);
    }

    let payload = tokens.to_credentials_json();
    let path = local_config::write_credentials(&args.source, &payload)?;

    Ok(ConnectGoogleResult {
        source: args.source,
        credentials_path: path.to_string_lossy().to_string(),
    })
}

#[tauri::command]
pub async fn disconnect_source(source: String) -> Result<(), String> {
    if matches!(source.as_str(), "gmail" | "gcal" | "gdrive") {
        let _ = keychain::delete("oauth.google.shared.refresh");
    } else {
        let _ = keychain::delete(&format!("oauth.{}", source));
    }
    let _ = local_config::delete_credentials(&source)?;
    Ok(())
}

#[tauri::command]
pub async fn write_credential(source: String, payload: Value) -> Result<Value, String> {
    let path = local_config::write_credentials(&source, &payload)?;
    Ok(json!({ "ok": true, "path": path }))
}

#[tauri::command]
pub fn keychain_set(account: String, secret: String) -> Result<(), String> {
    keychain::set(&account, &secret).map_err(err)
}

#[tauri::command]
pub fn keychain_get(account: String) -> Result<Option<String>, String> {
    keychain::get(&account).map_err(err)
}

#[tauri::command]
pub fn keychain_delete(account: String) -> Result<(), String> {
    keychain::delete(&account).map_err(err)
}

#[tauri::command]
pub async fn open_path(path: String, app: AppHandle) -> Result<(), String> {
    app.opener().open_path(&path, None::<&str>).map_err(err)
}

#[tauri::command]
pub async fn reveal_in_finder(path: String, app: AppHandle) -> Result<(), String> {
    app.opener()
        .reveal_item_in_dir(PathBuf::from(path))
        .map_err(err)
}

#[tauri::command]
pub async fn open_log_dir(app: AppHandle) -> Result<(), String> {
    let log_dir = local_config::daemon_log_path()?
        .parent()
        .map(PathBuf::from)
        .ok_or_else(|| "no log dir".to_string())?;
    app.opener()
        .open_path(log_dir.to_string_lossy().to_string(), None::<&str>)
        .map_err(err)
}

#[tauri::command]
pub async fn read_log_tail(lines: Option<usize>) -> Result<String, String> {
    let path = local_config::daemon_log_path()?;
    let content = match std::fs::read_to_string(&path) {
        Ok(content) => content,
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => return Ok(String::new()),
        Err(error) => return Err(error.to_string()),
    };
    let limit = lines.unwrap_or(200).max(1);
    let mut tail: Vec<&str> = content.lines().rev().take(limit).collect();
    tail.reverse();
    Ok(tail.join("\n"))
}

#[tauri::command]
pub fn quit_app(app: AppHandle) -> Result<(), String> {
    sidecar::shutdown(&app);
    app.exit(0);
    Ok(())
}

#[tauri::command]
pub fn recent_queries_get(app: AppHandle) -> Result<Vec<String>, String> {
    let store = app.store(STORE_FILE).map_err(err)?;
    let value = store.get("recent_queries").unwrap_or_default();
    Ok(value
        .as_array()
        .map(|items| {
            items
                .iter()
                .filter_map(|item| item.as_str().map(String::from))
                .collect()
        })
        .unwrap_or_default())
}

#[tauri::command]
pub fn recent_queries_push(query: String, app: AppHandle) -> Result<(), String> {
    let store = app.store(STORE_FILE).map_err(err)?;
    let mut current: Vec<String> = store
        .get("recent_queries")
        .and_then(|value| value.as_array().cloned())
        .map(|items| {
            items
                .into_iter()
                .filter_map(|item| item.as_str().map(String::from))
                .collect()
        })
        .unwrap_or_default();

    current.retain(|item| item != &query);
    current.insert(0, query);
    current.truncate(5);

    store.set(
        "recent_queries",
        Value::Array(current.into_iter().map(Value::from).collect()),
    );
    let _ = store.save();
    Ok(())
}

#[tauri::command]
pub fn onboarding_complete(app: AppHandle) -> Result<bool, String> {
    let store = app.store(STORE_FILE).map_err(err)?;
    Ok(store
        .get("onboarding_complete")
        .and_then(|value| value.as_bool())
        .unwrap_or(false))
}

#[tauri::command]
pub fn onboarding_set_complete(app: AppHandle, value: bool) -> Result<(), String> {
    let store = app.store(STORE_FILE).map_err(err)?;
    store.set("onboarding_complete", Value::Bool(value));
    let _ = store.save();
    Ok(())
}

fn combined_google_scopes(extra_scopes: Vec<String>) -> Vec<&'static str> {
    let _ = extra_scopes;
    vec![
        "https://www.googleapis.com/auth/gmail.readonly",
        "https://www.googleapis.com/auth/calendar.readonly",
        "https://www.googleapis.com/auth/drive.readonly",
    ]
}
