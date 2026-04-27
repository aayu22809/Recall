use std::sync::atomic::{AtomicU16, Ordering};
use std::time::Duration;

use serde::{Deserialize, Serialize};
use serde_json::Value;
use thiserror::Error;

#[derive(Debug, Error, Serialize)]
#[serde(tag = "kind", content = "msg")]
pub enum DaemonError {
    #[error("daemon unreachable: {0}")]
    Unreachable(String),
    #[error("daemon error: {0}")]
    Server(String),
}

impl From<reqwest::Error> for DaemonError {
    fn from(error: reqwest::Error) -> Self {
        if error.is_connect() || error.is_timeout() {
            Self::Unreachable(error.to_string())
        } else {
            Self::Server(error.to_string())
        }
    }
}

pub struct DaemonClient {
    port: AtomicU16,
    http: reqwest::Client,
}

impl Default for DaemonClient {
    fn default() -> Self {
        Self {
            port: AtomicU16::new(19847),
            http: reqwest::Client::builder()
                .timeout(Duration::from_secs(20))
                .build()
                .expect("reqwest client init"),
        }
    }
}

impl DaemonClient {
    pub fn port(&self) -> u16 {
        self.port.load(Ordering::Relaxed)
    }

    pub fn set_port(&self, port: u16) {
        self.port.store(port, Ordering::Relaxed);
    }

    fn url(&self, path: &str) -> String {
        format!("http://127.0.0.1:{}{}", self.port(), path)
    }

    async fn get<T: serde::de::DeserializeOwned>(&self, path: &str) -> Result<T, DaemonError> {
        let response = self.http.get(self.url(path)).send().await?;
        self.decode(response).await
    }

    async fn send_json<T: serde::de::DeserializeOwned>(
        &self,
        method: reqwest::Method,
        path: &str,
        payload: &Value,
    ) -> Result<T, DaemonError> {
        let response = self
            .http
            .request(method, self.url(path))
            .json(payload)
            .send()
            .await?;
        self.decode(response).await
    }

    async fn decode<T: serde::de::DeserializeOwned>(
        &self,
        response: reqwest::Response,
    ) -> Result<T, DaemonError> {
        if !response.status().is_success() {
            let body = response.text().await.unwrap_or_default();
            return Err(DaemonError::Server(body));
        }
        response.json().await.map_err(DaemonError::from)
    }

    pub async fn stats(&self) -> Result<Stats, DaemonError> {
        self.get("/stats").await
    }

    pub async fn sources(&self) -> Result<SourcesResponse, DaemonError> {
        self.get("/sources").await
    }

    pub async fn progress(&self) -> Result<Progress, DaemonError> {
        self.get("/progress").await
    }

    pub async fn index_status(&self) -> Result<IndexStatus, DaemonError> {
        self.get("/index/status").await
    }

    pub async fn connector_status(&self) -> Result<Value, DaemonError> {
        self.get("/connector-status").await
    }

    pub async fn sync(&self, source: Option<&str>) -> Result<Value, DaemonError> {
        let payload = match source {
            Some(source) => serde_json::json!({ "source": source }),
            None => serde_json::json!({}),
        };
        self.send_json(reqwest::Method::POST, "/sync", &payload).await
    }

    pub async fn sync_running(&self) -> Result<bool, DaemonError> {
        let response: Value = self.get("/sync-running").await?;
        Ok(response
            .get("running")
            .and_then(|value| value.as_bool())
            .unwrap_or(false))
    }

    pub async fn watched_dirs(&self) -> Result<WatchedDirs, DaemonError> {
        self.get("/watched-dirs").await
    }

    pub async fn watched_dirs_stats(&self) -> Result<Value, DaemonError> {
        self.get("/watched-dirs/stats").await
    }

    pub async fn add_watched_dir(&self, path: &str) -> Result<WatchedDirs, DaemonError> {
        self.send_json(
            reqwest::Method::POST,
            "/watched-dirs",
            &serde_json::json!({ "path": path }),
        )
        .await
    }

    pub async fn remove_watched_dir(&self, path: &str) -> Result<WatchedDirs, DaemonError> {
        self.send_json(
            reqwest::Method::DELETE,
            "/watched-dirs",
            &serde_json::json!({ "path": path }),
        )
        .await
    }

    pub async fn index_watched_dirs(&self) -> Result<Value, DaemonError> {
        self.send_json(reqwest::Method::POST, "/index/watched-dirs", &serde_json::json!({}))
            .await
    }

    pub async fn search(
        &self,
        query: &str,
        n_results: usize,
        sources: Option<Vec<String>>,
    ) -> Result<Vec<SearchResult>, DaemonError> {
        let mut payload = serde_json::json!({
            "query": query,
            "n_results": n_results,
        });
        if let Some(sources) = sources {
            payload["sources"] = serde_json::json!(sources);
        }
        self.send_json(reqwest::Method::POST, "/search", &payload).await
    }

    pub async fn ingest(
        &self,
        path: &str,
        source: &str,
        description: &str,
    ) -> Result<Value, DaemonError> {
        self.send_json(
            reqwest::Method::POST,
            "/ingest",
            &serde_json::json!({
                "path": path,
                "source": source,
                "description": description,
            }),
        )
        .await
    }
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Stats {
    pub status: String,
    pub count: u64,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct SourcesResponse {
    pub sources: Vec<String>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Progress {
    pub indexing: bool,
    pub queued: u64,
    pub total_indexed: u64,
    #[serde(default)]
    pub processed: u64,
    #[serde(default)]
    pub embedded: u64,
    #[serde(default)]
    pub skipped: u64,
    #[serde(default)]
    pub errors: u64,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct WatchedDirs {
    pub dirs: Vec<String>,
    #[serde(default)]
    pub restart_required: bool,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct SearchResult {
    pub id: String,
    pub similarity: f32,
    pub file_path: String,
    pub file_name: String,
    pub media_category: String,
    pub timestamp: String,
    pub description: String,
    pub source: String,
    pub preview: String,
    #[serde(default)]
    pub metadata: Value,
}

#[derive(Debug, Clone, Serialize, Deserialize, Default)]
pub struct IndexStatus {
    pub running: bool,
    pub queued: u64,
    pub processed: u64,
    pub embedded: u64,
    pub skipped: u64,
    pub errors: u64,
    pub active_path: Option<String>,
    pub started_at: Option<String>,
    pub finished_at: Option<String>,
    #[serde(default)]
    pub last_error: Option<String>,
}
