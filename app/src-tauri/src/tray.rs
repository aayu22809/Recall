//! Menu-bar tray icon — three states (running / starting / stopped).

use tauri::menu::{Menu, MenuItem};
use tauri::tray::{MouseButton, MouseButtonState, TrayIconBuilder, TrayIconEvent};
use tauri::{AppHandle, Manager};

pub fn install(app: &AppHandle) -> tauri::Result<()> {
    let open = MenuItem::with_id(app, "open", "Open Recall", true, None::<&str>)?;
    let search = MenuItem::with_id(app, "search", "Search…   ⌥Space", true, None::<&str>)?;
    let sync = MenuItem::with_id(app, "sync", "Sync now", true, None::<&str>)?;
    let sep = tauri::menu::PredefinedMenuItem::separator(app)?;
    let quit = MenuItem::with_id(app, "quit", "Quit Recall", true, None::<&str>)?;
    let menu = Menu::with_items(app, &[&open, &search, &sync, &sep, &quit])?;

    let _tray = TrayIconBuilder::with_id("recall-tray")
        .menu(&menu)
        .show_menu_on_left_click(true)
        .icon(app.default_window_icon().cloned().unwrap())
        .on_menu_event(|app, event| match event.id.as_ref() {
            "open" => {
                if let Some(w) = app.get_webview_window("main") {
                    let _ = w.show();
                    let _ = w.set_focus();
                }
            }
            "search" => {
                if let Some(w) = app.get_webview_window("spotlight") {
                    let _ = w.show();
                    let _ = w.set_focus();
                }
            }
            "sync" => {
                let app2 = app.clone();
                tauri::async_runtime::spawn(async move {
                    let _ = crate::sidecar::ensure_running(&app2, std::time::Duration::from_secs(20)).await;
                    if let Some(client) = app2.try_state::<crate::daemon_client::DaemonClient>() {
                        let _ = client.index_watched_dirs().await;
                        let _ = client.sync(None).await;
                    }
                });
            }
            "quit" => {
                crate::sidecar::shutdown(app);
                app.exit(0);
            }
            _ => {}
        })
        .on_tray_icon_event(|tray, event| {
            if let TrayIconEvent::Click {
                button: MouseButton::Left,
                button_state: MouseButtonState::Up,
                ..
            } = event
            {
                let app = tray.app_handle();
                if let Some(w) = app.get_webview_window("main") {
                    let _ = w.show();
                    let _ = w.set_focus();
                }
            }
        })
        .build(app)?;

    Ok(())
}
