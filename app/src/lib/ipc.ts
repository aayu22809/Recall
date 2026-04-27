// Thin typed wrapper over Tauri's invoke() so the routes don't import the
// Tauri API directly. Lets us swap in a mock during Storybook / Vite without
// the Tauri runtime.

import { invoke as tauriInvoke } from "@tauri-apps/api/core";

export async function invoke<T>(cmd: string, args?: Record<string, unknown>): Promise<T> {
  return tauriInvoke<T>(cmd, args);
}

export { listen } from "@tauri-apps/api/event";
