//! Window setup — show the main window only after the daemon is healthy so
//! the search input never has to render the "Warming index…" placeholder for
//! more than the first sub-second tick.

use tauri::{AppHandle, Manager};

pub fn on_setup(app: &AppHandle) -> tauri::Result<()> {
    if let Some(main) = app.get_webview_window("main") {
        // Show the window immediately — onboarding handles the warm-up state
        // visually so the user gets feedback even on a cold ChromaDB load.
        let _ = main.show();
        let _ = main.set_focus();
    }
    if let Some(spot) = app.get_webview_window("spotlight") {
        let _ = spot.hide();
    }
    Ok(())
}
