use std::sync::Mutex;
use std::time::{Duration, Instant};

use serde::Serialize;
use tauri::{AppHandle, Emitter, Manager};
use tauri_plugin_shell::process::{CommandChild, CommandEvent};
use tauri_plugin_shell::ShellExt;
use tauri_plugin_store::StoreExt;
use tokio::sync::Mutex as AsyncMutex;

use crate::daemon_client::DaemonClient;
use crate::local_config;

const DEFAULT_PORT: u16 = 19847;
const FALLBACK_PORT_RANGE: std::ops::Range<u16> = 19848..19900;
const STORE_FILE: &str = "recall.app.store.json";
const PORT_KEY: &str = "daemon_port";
const STATUS_EVENT: &str = "daemon://status";

#[derive(Clone, Copy, Debug, Serialize, PartialEq, Eq)]
#[serde(rename_all = "snake_case")]
pub enum Status {
    Starting,
    Healthy,
    Degraded,
    Stopped,
}

#[derive(Clone, Debug, Serialize)]
pub struct StatusPayload {
    pub status: Status,
    pub port: u16,
    pub attached: bool,
    pub message: Option<String>,
    pub restart_count: u32,
    pub log_path: String,
}

struct ControllerInner {
    child: Option<CommandChild>,
    status: Status,
    port: u16,
    attached: bool,
    last_error: Option<String>,
    restart_count: u32,
    log_path: String,
    recent_output: String,
}

pub struct DaemonController {
    inner: Mutex<ControllerInner>,
    start_lock: AsyncMutex<()>,
}

impl Default for DaemonController {
    fn default() -> Self {
        let log_path = local_config::daemon_log_path()
            .unwrap_or_else(|_| std::path::PathBuf::from("~/.vef/daemon.log"))
            .to_string_lossy()
            .to_string();
        Self {
            inner: Mutex::new(ControllerInner {
                child: None,
                status: Status::Stopped,
                port: DEFAULT_PORT,
                attached: false,
                last_error: None,
                restart_count: 0,
                log_path,
                recent_output: String::new(),
            }),
            start_lock: AsyncMutex::new(()),
        }
    }
}

impl DaemonController {
    pub fn snapshot(&self) -> StatusPayload {
        let inner = self.inner.lock().expect("daemon controller lock");
        StatusPayload {
            status: inner.status,
            port: inner.port,
            attached: inner.attached,
            message: inner.last_error.clone(),
            restart_count: inner.restart_count,
            log_path: inner.log_path.clone(),
        }
    }

    fn has_child(&self) -> bool {
        self.inner
            .lock()
            .expect("daemon controller lock")
            .child
            .is_some()
    }

    fn store_child(&self, child: CommandChild) {
        let mut inner = self.inner.lock().expect("daemon controller lock");
        if let Some(existing) = inner.child.take() {
            let _ = existing.kill();
        }
        inner.child = Some(child);
        inner.attached = false;
    }

    fn kill_child(&self) {
        let mut inner = self.inner.lock().expect("daemon controller lock");
        if let Some(child) = inner.child.take() {
            let _ = child.kill();
        }
    }

    fn set_state(
        &self,
        status: Status,
        port: u16,
        attached: bool,
        message: Option<String>,
    ) -> StatusPayload {
        let mut inner = self.inner.lock().expect("daemon controller lock");
        inner.status = status;
        inner.port = port;
        inner.attached = attached;
        inner.last_error = message;
        StatusPayload {
            status: inner.status,
            port: inner.port,
            attached: inner.attached,
            message: inner.last_error.clone(),
            restart_count: inner.restart_count,
            log_path: inner.log_path.clone(),
        }
    }

    fn append_output(&self, chunk: &str) {
        let trimmed = chunk.trim();
        if trimmed.is_empty() {
            return;
        }
        let mut inner = self.inner.lock().expect("daemon controller lock");
        if !inner.recent_output.is_empty() {
            inner.recent_output.push('\n');
        }
        inner.recent_output.push_str(trimmed);
        const MAX_OUTPUT_CHARS: usize = 8_000;
        if inner.recent_output.len() > MAX_OUTPUT_CHARS {
            let start = inner.recent_output.len() - MAX_OUTPUT_CHARS;
            inner.recent_output = inner.recent_output[start..].to_string();
        }
    }

    fn reset_output(&self) {
        let mut inner = self.inner.lock().expect("daemon controller lock");
        inner.recent_output.clear();
    }

    fn recent_output(&self) -> Option<String> {
        let inner = self.inner.lock().expect("daemon controller lock");
        if inner.recent_output.trim().is_empty() {
            None
        } else {
            Some(inner.recent_output.clone())
        }
    }

    fn bump_restart_count(&self) {
        let mut inner = self.inner.lock().expect("daemon controller lock");
        inner.restart_count += 1;
    }
}

pub fn spawn_and_supervise(app: AppHandle) {
    let startup = app.clone();
    tauri::async_runtime::spawn(async move {
        let _ = ensure_running(&startup, Duration::from_secs(20)).await;
    });

    tauri::async_runtime::spawn(async move {
        let mut degraded_since: Option<Instant> = None;
        loop {
            tokio::time::sleep(Duration::from_secs(5)).await;
            let snapshot = daemon_controller(&app).snapshot();
            if snapshot.port == 0 {
                continue;
            }

            let healthy = is_our_daemon(snapshot.port).await.unwrap_or(false);
            if healthy {
                degraded_since = None;
                if snapshot.status != Status::Healthy {
                    let payload = daemon_controller(&app).set_state(
                        Status::Healthy,
                        snapshot.port,
                        snapshot.attached,
                        None,
                    );
                    emit_status(&app, payload);
                }
                continue;
            }

            if snapshot.status == Status::Starting {
                continue;
            }

            if degraded_since.is_none() {
                degraded_since = Some(Instant::now());
            }

            let payload = daemon_controller(&app).set_state(
                Status::Degraded,
                snapshot.port,
                snapshot.attached,
                Some("daemon health check failed".to_string()),
            );
            emit_status(&app, payload.clone());

            if !snapshot.attached
                && daemon_controller(&app).has_child()
                && degraded_since
                    .map(|started| started.elapsed() > Duration::from_secs(15))
                    .unwrap_or(false)
            {
                let _ = restart(&app, Duration::from_secs(20)).await;
                degraded_since = None;
            }
        }
    });
}

pub async fn ensure_running(app: &AppHandle, timeout: Duration) -> Result<StatusPayload, String> {
    let controller = daemon_controller(app);
    let _start_guard = controller.start_lock.lock().await;
    let snapshot = controller.snapshot();

    if snapshot.port != 0 && is_our_daemon(snapshot.port).await.unwrap_or(false) {
        update_client_base(app, snapshot.port);
        let payload = controller.set_state(
            Status::Healthy,
            snapshot.port,
            snapshot.attached,
            None,
        );
        emit_status(app, payload.clone());
        return Ok(payload);
    }

    let port = if snapshot.port != 0
        && (can_bind(snapshot.port).await || is_our_daemon(snapshot.port).await.unwrap_or(false))
    {
        snapshot.port
    } else {
        negotiate_port(app).await?
    };

    persist_port(app, port);
    update_client_base(app, port);

    if is_our_daemon(port).await.unwrap_or(false) {
        let payload = daemon_controller(app).set_state(Status::Healthy, port, true, None);
        emit_status(app, payload.clone());
        return Ok(payload);
    }

    let payload = controller.set_state(Status::Starting, port, false, None);
    controller.reset_output();
    emit_status(app, payload);

    if !controller.has_child() {
        let child = launch(app, port).await?;
        controller.store_child(child);
    }

    wait_until_healthy(app, port, timeout).await
}

pub async fn restart(app: &AppHandle, timeout: Duration) -> Result<StatusPayload, String> {
    let controller = daemon_controller(app);
    let _start_guard = controller.start_lock.lock().await;
    let snapshot = controller.snapshot();

    if snapshot.attached && is_our_daemon(snapshot.port).await.unwrap_or(false) {
        return Ok(snapshot);
    }

    controller.kill_child();
    controller.bump_restart_count();

    let port = if snapshot.port != 0 { snapshot.port } else { negotiate_port(app).await? };
    persist_port(app, port);
    update_client_base(app, port);

    let payload = controller.set_state(Status::Starting, port, false, None);
    controller.reset_output();
    emit_status(app, payload);

    let child = launch(app, port).await?;
    controller.store_child(child);
    wait_until_healthy(app, port, timeout).await
}

pub fn shutdown(app: &AppHandle) {
    daemon_controller(app).kill_child();
    let payload = daemon_controller(app).set_state(Status::Stopped, 0, false, None);
    emit_status(app, payload);
}

fn daemon_controller(app: &AppHandle) -> tauri::State<'_, DaemonController> {
    app.state::<DaemonController>()
}

async fn wait_until_healthy(
    app: &AppHandle,
    port: u16,
    timeout: Duration,
) -> Result<StatusPayload, String> {
    let started = Instant::now();
    while started.elapsed() <= timeout {
        if is_our_daemon(port).await.unwrap_or(false) {
            let payload = daemon_controller(app).set_state(Status::Healthy, port, false, None);
            emit_status(app, payload.clone());
            return Ok(payload);
        }
        tokio::time::sleep(Duration::from_millis(250)).await;
    }

    let snapshot = daemon_controller(app).snapshot();
    let message = daemon_controller(app)
        .recent_output()
        .or(snapshot.message)
        .unwrap_or_else(|| format!("daemon did not become healthy within {}s", timeout.as_secs()));
    daemon_controller(app).kill_child();
    let payload = daemon_controller(app).set_state(Status::Stopped, port, false, Some(message.clone()));
    emit_status(app, payload);
    Err(message)
}

async fn negotiate_port(app: &AppHandle) -> Result<u16, String> {
    if let Ok(stored) = app
        .store(STORE_FILE)
        .map_err(|e| e.to_string())
        .and_then(|s| {
            s.get(PORT_KEY)
                .and_then(|v| v.as_u64())
                .map(|n| n as u16)
                .ok_or_else(|| "no stored port".to_string())
        })
    {
        if can_bind(stored).await || is_our_daemon(stored).await.unwrap_or(false) {
            return Ok(stored);
        }
    }

    if can_bind(DEFAULT_PORT).await || is_our_daemon(DEFAULT_PORT).await.unwrap_or(false) {
        return Ok(DEFAULT_PORT);
    }

    for port in FALLBACK_PORT_RANGE {
        if can_bind(port).await {
            return Ok(port);
        }
    }

    Err("could not find a free port in 19847..19899".to_string())
}

fn persist_port(app: &AppHandle, port: u16) {
    if let Ok(store) = app.store(STORE_FILE) {
        store.set(PORT_KEY, serde_json::json!(port));
        let _ = store.save();
    }
}

async fn can_bind(port: u16) -> bool {
    tokio::net::TcpListener::bind(("127.0.0.1", port))
        .await
        .is_ok()
}

async fn is_our_daemon(port: u16) -> Result<bool, ()> {
    let url = format!("http://127.0.0.1:{port}/health");
    let resp = reqwest::Client::builder()
        .timeout(Duration::from_secs(2))
        .build()
        .map_err(|_| ())?
        .get(&url)
        .send()
        .await
        .map_err(|_| ())?;
    if !resp.status().is_success() {
        return Ok(false);
    }
    let body: serde_json::Value = resp.json().await.map_err(|_| ())?;
    Ok(body
        .get("service")
        .and_then(|v| v.as_str())
        .map(|service| service == "recall-daemon")
        .unwrap_or(false))
}

async fn launch(app: &AppHandle, port: u16) -> Result<CommandChild, String> {
    let cmd = app
        .shell()
        .sidecar("recall-daemon")
        .map_err(|e| e.to_string())?
        .args(["_serve"])
        .env("RECALL_PORT", port.to_string())
        .env("VEF_PORT", port.to_string());

    let (mut rx, child) = cmd.spawn().map_err(|e| e.to_string())?;
    let app_for_events = app.clone();

    tauri::async_runtime::spawn(async move {
        while let Some(event) = rx.recv().await {
            match event {
                CommandEvent::Stdout(line) | CommandEvent::Stderr(line) => {
                    let rendered = String::from_utf8_lossy(&line);
                    daemon_controller(&app_for_events).append_output(&rendered);
                    log::debug!("[daemon] {}", rendered.trim_end());
                }
                CommandEvent::Terminated(payload) => {
                    let message = daemon_controller(&app_for_events)
                        .recent_output()
                        .unwrap_or_else(|| format!("daemon exited: {payload:?}"));
                    daemon_controller(&app_for_events).kill_child();
                    let payload = daemon_controller(&app_for_events).set_state(
                        Status::Stopped,
                        0,
                        false,
                        Some(message),
                    );
                    emit_status(&app_for_events, payload);
                    break;
                }
                _ => {}
            }
        }
    });

    Ok(child)
}

fn update_client_base(app: &AppHandle, port: u16) {
    app.state::<DaemonClient>().set_port(port);
}

fn emit_status(app: &AppHandle, payload: StatusPayload) {
    let _ = app.emit(STATUS_EVENT, payload);
}
