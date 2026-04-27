//! Thin wrapper over the `keyring` crate, scoped to a single service id so we
//! can inventory and clear all Recall-managed secrets in one shot.

use keyring::Entry;

const SERVICE: &str = "ai.recall.app";

#[derive(Debug, thiserror::Error)]
pub enum KeychainError {
    #[error("keychain: {0}")]
    Backend(#[from] keyring::Error),
}

pub fn set(account: &str, secret: &str) -> Result<(), KeychainError> {
    Entry::new(SERVICE, account)?.set_password(secret)?;
    Ok(())
}

pub fn get(account: &str) -> Result<Option<String>, KeychainError> {
    match Entry::new(SERVICE, account)?.get_password() {
        Ok(s) => Ok(Some(s)),
        Err(keyring::Error::NoEntry) => Ok(None),
        Err(e) => Err(e.into()),
    }
}

pub fn delete(account: &str) -> Result<(), KeychainError> {
    match Entry::new(SERVICE, account)?.delete_credential() {
        Ok(_) | Err(keyring::Error::NoEntry) => Ok(()),
        Err(e) => Err(e.into()),
    }
}
