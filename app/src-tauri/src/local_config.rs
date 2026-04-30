use std::collections::BTreeMap;
use std::path::PathBuf;

use serde::Serialize;
use serde_json::Value;

const GOOGLE_SHARED_CREDENTIAL_FILE: &str = "gmail.json";

#[derive(Debug, Clone, Serialize)]
pub struct ConfigApplyResult {
    pub ok: bool,
    pub restarted: bool,
    pub env_path: String,
}

pub fn env_path() -> Result<PathBuf, String> {
    dirs::home_dir()
        .map(|dir| dir.join(".vef").join(".env"))
        .ok_or_else(|| "no home dir".to_string())
}

pub fn daemon_log_path() -> Result<PathBuf, String> {
    dirs::home_dir()
        .map(|dir| dir.join(".vef").join("daemon.log"))
        .ok_or_else(|| "no home dir".to_string())
}

pub fn credentials_dir() -> Result<PathBuf, String> {
    dirs::home_dir()
        .map(|dir| dir.join(".vef").join("credentials"))
        .ok_or_else(|| "no home dir".to_string())
}

pub fn apply_config_updates(payload: &Value) -> Result<Vec<String>, String> {
    let Some(obj) = payload.as_object() else {
        return Err("configure payload must be a JSON object".to_string());
    };

    let path = env_path()?;
    if let Some(parent) = path.parent() {
        std::fs::create_dir_all(parent).map_err(|e| e.to_string())?;
    }

    let mut env_map = read_env_map(&path)?;
    let mut changed = Vec::new();

    for (key, value) in obj {
        let Some(env_key) = config_key_to_env(key) else {
            continue;
        };
        let rendered = render_env_value(value)
            .map_err(|e| format!("invalid value for {key}: {e}"))?;

        if env_map.get(env_key) != Some(&rendered) {
            env_map.insert(env_key.to_string(), rendered.clone());
            std::env::set_var(env_key, &rendered);
            changed.push(env_key.to_string());
        }
    }

    write_env_map(&path, &env_map)?;
    Ok(changed)
}

pub fn config_change_requires_restart(changed_keys: &[String]) -> bool {
    changed_keys.iter().any(|key| {
        matches!(
            key.as_str(),
            "GEMINI_API_KEY"
                | "NIM_API_KEY"
                | "VEF_EMBEDDING_PROVIDER"
                | "VEF_EMBEDDING_MODEL"
                | "VEF_EMBEDDING_DIMENSIONS"
                | "VEF_OLLAMA_BASE_URL"
                | "VEF_OLLAMA_EMBED_MODEL"
                | "VEF_NIM_EMBED_URL"
                | "VEF_NIM_EMBED_MODEL"
        )
    })
}

pub fn write_credentials(source: &str, payload: &Value) -> Result<PathBuf, String> {
    if !payload.is_object() {
        return Err("credential payload must be a JSON object".to_string());
    }
    let target = credential_path_for_source(source)?;
    if let Some(parent) = target.parent() {
        std::fs::create_dir_all(parent).map_err(|e| e.to_string())?;
    }
    let body = serde_json::to_string_pretty(payload).map_err(|e| e.to_string())?;
    std::fs::write(&target, body).map_err(|e| e.to_string())?;
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        let _ = std::fs::set_permissions(&target, std::fs::Permissions::from_mode(0o600));
    }
    Ok(target)
}

pub fn delete_credentials(source: &str) -> Result<PathBuf, String> {
    let target = credential_path_for_source(source)?;
    if target.exists() {
        std::fs::remove_file(&target).map_err(|e| e.to_string())?;
    }
    Ok(target)
}

fn credential_path_for_source(source: &str) -> Result<PathBuf, String> {
    let normalized = source.trim().to_lowercase();
    let file_name = match normalized.as_str() {
        "gmail" | "gcal" | "gdrive" => GOOGLE_SHARED_CREDENTIAL_FILE,
        "canvas" => "canvas.json",
        "schoology" => "schoology.json",
        "calai" => "calai.json",
        "notion" => "notion.json",
        _ => return Err(format!("unknown source '{source}'")),
    };
    Ok(credentials_dir()?.join(file_name))
}

fn read_env_map(path: &PathBuf) -> Result<BTreeMap<String, String>, String> {
    let mut env_map = BTreeMap::new();
    let content = match std::fs::read_to_string(path) {
        Ok(content) => content,
        Err(err) if err.kind() == std::io::ErrorKind::NotFound => return Ok(env_map),
        Err(err) => return Err(err.to_string()),
    };

    for raw_line in content.lines() {
        let line = raw_line.trim();
        if line.is_empty() || line.starts_with('#') {
            continue;
        }
        if let Some((key, value)) = line.split_once('=') {
            env_map.insert(key.trim().to_string(), value.trim().to_string());
        }
    }
    Ok(env_map)
}

fn write_env_map(path: &PathBuf, env_map: &BTreeMap<String, String>) -> Result<(), String> {
    let body = if env_map.is_empty() {
        String::new()
    } else {
        let mut lines = Vec::with_capacity(env_map.len());
        for (key, value) in env_map {
            lines.push(format!("{key}={value}"));
        }
        format!("{}\n", lines.join("\n"))
    };
    std::fs::write(path, body).map_err(|e| e.to_string())
}

fn render_env_value(value: &Value) -> Result<String, &'static str> {
    match value {
        Value::Null => Err("null is not supported"),
        Value::Bool(v) => Ok(v.to_string()),
        Value::Number(v) => Ok(v.to_string()),
        Value::String(v) => Ok(v.trim().to_string()),
        Value::Array(_) | Value::Object(_) => Err("nested JSON is not supported"),
    }
}

fn config_key_to_env(key: &str) -> Option<&'static str> {
    match key {
        "gemini_api_key" => Some("GEMINI_API_KEY"),
        "nim_api_key" => Some("NIM_API_KEY"),
        "canvas_api_key" => Some("CANVAS_API_KEY"),
        "canvas_base_url" => Some("CANVAS_BASE_URL"),
        "schoology_consumer_key" => Some("SCHOOLOGY_CONSUMER_KEY"),
        "schoology_consumer_secret" => Some("SCHOOLOGY_CONSUMER_SECRET"),
        "schoology_base_url" => Some("SCHOOLOGY_BASE_URL"),
        "vef_embedding_provider" => Some("VEF_EMBEDDING_PROVIDER"),
        "vef_embedding_model" => Some("VEF_EMBEDDING_MODEL"),
        "vef_embedding_dimensions" => Some("VEF_EMBEDDING_DIMENSIONS"),
        "vef_ollama_base_url" => Some("VEF_OLLAMA_BASE_URL"),
        "vef_ollama_embed_model" => Some("VEF_OLLAMA_EMBED_MODEL"),
        "vef_nim_embed_url" => Some("VEF_NIM_EMBED_URL"),
        "vef_nim_embed_model" => Some("VEF_NIM_EMBED_MODEL"),
        _ => None,
    }
}
