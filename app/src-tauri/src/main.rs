// Recall macOS shell — Tauri 2 entry point.
// All logic lives in lib.rs so the integration tests can link against it.

#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

fn main() {
    recall_app_lib::run();
}
