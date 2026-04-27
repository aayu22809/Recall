//! In-app OAuth flows (no Cloud Console required).
//!
//! Currently houses only the Google PKCE+loopback flow used for Gmail, Google
//! Calendar, and Google Drive. The shape of the JSON written to
//! `~/.vef/credentials/<source>.json` matches what the existing Python
//! connectors pass to `Credentials.from_authorized_user_info`, so the
//! connectors don't need any code changes beyond the small short-circuit in
//! `authenticate()` that's already landed.

pub mod google;
