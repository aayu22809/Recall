//! Recall — Tauri 2 shell that wraps the Python daemon as a sidecar.
//!
//! Responsibilities of the Rust layer:
//!   * Sidecar lifecycle (spawn, port negotiation, health watch, restart, kill)
//!   * Typed daemon HTTP client re-exposed as `#[tauri::command]`s
//!   * In-app OAuth via PKCE loopback (Gmail / GCal / GDrive)
//!   * macOS Keychain access via the `keyring` crate
//!   * Tray icon, ⌥Space global hotkey, deep-link routing for `recall://`
//!
//! The Python daemon is launched with `RECALL_PORT` injected; if a foreign
//! listener is already on 19847 the shell walks 19848..19899 for a free port
//! and stores the choice via `tauri-plugin-store`.

mod commands;
mod daemon_client;
mod hotkey;
mod keychain;
mod local_config;
mod oauth;
mod sidecar;
mod tray;
mod windows;

use tauri::Manager;

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    tauri::Builder::default()
        .plugin(tauri_plugin_single_instance::init(|app, _argv, _cwd| {
            if let Some(win) = app.get_webview_window("main") {
                let _ = win.show();
                let _ = win.set_focus();
            }
        }))
        .plugin(tauri_plugin_shell::init())
        .plugin(tauri_plugin_dialog::init())
        .plugin(tauri_plugin_fs::init())
        .plugin(tauri_plugin_store::Builder::new().build())
        .plugin(tauri_plugin_notification::init())
        .plugin(tauri_plugin_log::Builder::new().build())
        .plugin(tauri_plugin_os::init())
        .plugin(tauri_plugin_process::init())
        .plugin(tauri_plugin_opener::init())
        .plugin(tauri_plugin_deep_link::init())
        .plugin(tauri_plugin_global_shortcut::Builder::new().build())
        .plugin(tauri_plugin_updater::Builder::new().build())
        .manage(daemon_client::DaemonClient::default())
        .manage(sidecar::DaemonController::default())
        .invoke_handler(tauri::generate_handler![
            commands::daemon_status,
            commands::daemon_ensure_running,
            commands::daemon_restart,
            commands::search,
            commands::stats,
            commands::sources,
            commands::progress,
            commands::index_status,
            commands::connector_status,
            commands::sync,
            commands::sync_running,
            commands::watched_dirs,
            commands::watched_dirs_stats,
            commands::add_watched_dir,
            commands::remove_watched_dir,
            commands::index_watched_dirs,
            commands::ingest,
            commands::configure,
            commands::ollama_detect,
            commands::oauth_connect_google,
            commands::disconnect_source,
            commands::write_credential,
            commands::keychain_set,
            commands::keychain_get,
            commands::keychain_delete,
            commands::open_path,
            commands::reveal_in_finder,
            commands::open_log_dir,
            commands::read_log_tail,
            commands::quit_app,
            commands::recent_queries_get,
            commands::recent_queries_push,
            commands::onboarding_complete,
            commands::onboarding_set_complete,
        ])
        .setup(|app| {
            let handle = app.handle().clone();
            sidecar::spawn_and_supervise(handle.clone());
            tray::install(&handle)?;
            hotkey::install(&handle)?;
            windows::on_setup(&handle)?;
            Ok(())
        })
        .run(tauri::generate_context!())
        .expect("error while running Recall application");
}
