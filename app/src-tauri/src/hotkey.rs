//! Global hotkey for the spotlight overlay.
//!
//! Default binding: ⌥Space. The first time the app launches we register it
//! optimistically — if another app holds the binding macOS will reject the
//! registration and we silently move on. v0.4.1 will surface a settings UI to
//! pick a different binding.

use tauri::{AppHandle, Manager};
use tauri_plugin_global_shortcut::{Code, GlobalShortcutExt, Modifiers, Shortcut, ShortcutState};

pub fn install(app: &AppHandle) -> tauri::Result<()> {
    let shortcut = Shortcut::new(Some(Modifiers::ALT), Code::Space);
    if let Err(e) = app
        .global_shortcut()
        .on_shortcut(shortcut, |app, _scut, event| {
            if event.state() == ShortcutState::Pressed {
                toggle_spotlight(app);
            }
        })
    {
        log::warn!("failed to register global shortcut: {e}");
    }
    Ok(())
}

fn toggle_spotlight(app: &AppHandle) {
    if let Some(win) = app.get_webview_window("spotlight") {
        match win.is_visible() {
            Ok(true) => {
                let _ = win.hide();
            }
            _ => {
                let _ = win.show();
                let _ = win.set_focus();
            }
        }
    }
}
