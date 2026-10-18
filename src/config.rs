//! Configuration management with multi-source loading.
//!
//! Priority (highest to lowest): explicit overrides (CLI), `HASHCLI_*` environment variables,
//! `--config-file`, user config (`~/.hashcli/config.toml`), system config, defaults.

use std::collections::BTreeMap;
use std::fmt;
use std::path::{Path, PathBuf};

use serde::{Deserialize, Serialize};
use toml::Value;

#[derive(Debug, thiserror::Error)]
#[error("{0}")]
pub struct ConfigError(pub String);

impl ConfigError {
    pub fn new(message: impl Into<String>) -> Self {
        ConfigError(message.into())
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "lowercase")]
pub enum LlmProvider {
    Openai,
    Anthropic,
    Google,
}

impl LlmProvider {
    pub const ALL: [LlmProvider; 3] = [LlmProvider::Openai, LlmProvider::Anthropic, LlmProvider::Google];

    pub fn as_str(&self) -> &'static str {
        match self {
            LlmProvider::Openai => "openai",
            LlmProvider::Anthropic => "anthropic",
            LlmProvider::Google => "google",
        }
    }

    pub fn parse(value: &str) -> Option<Self> {
        match value.trim().to_lowercase().as_str() {
            "openai" => Some(LlmProvider::Openai),
            "anthropic" => Some(LlmProvider::Anthropic),
            "google" => Some(LlmProvider::Google),
            _ => None,
        }
    }

    pub fn display_name(&self) -> &'static str {
        match self {
            LlmProvider::Openai => "Openai",
            LlmProvider::Anthropic => "Anthropic",
            LlmProvider::Google => "Google",
        }
    }
}

impl fmt::Display for LlmProvider {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str(self.as_str())
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "lowercase")]
pub enum LogLevel {
    Debug,
    Info,
    Warning,
    Error,
}

/// Main configuration object.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(default)]
pub struct HashConfig {
    // LLM configuration
    pub llm_provider: LlmProvider,
    pub openai_api_key: Option<String>,
    pub openai_base_url: Option<String>,
    pub openai_model: String,
    pub openai_reasoning_effort: Option<String>,
    pub openai_text_verbosity: Option<String>,
    pub anthropic_api_key: Option<String>,
    pub anthropic_model: String,
    pub google_api_key: Option<String>,
    pub google_model: String,
    pub max_response_tokens: i64,

    // Tool configuration
    pub allow_command_execution: bool,
    pub command_confirmation: bool,
    pub tool_confirmation: bool,
    pub command_timeout: i64,
    pub allow_shell_operators: bool,

    // History configuration
    pub history_enabled: bool,
    pub history_dir: PathBuf,
    pub max_history_size: i64,
    pub history_retention_days: i64,

    // Output configuration
    pub rich_output: bool,
    pub streaming: bool,
    pub show_debug: bool,
    pub log_level: LogLevel,

    // Security configuration
    pub sandbox_commands: bool,
    pub allowed_commands: Option<Vec<String>>,
    pub blocked_commands: Vec<String>,
}

impl Default for HashConfig {
    fn default() -> Self {
        HashConfig {
            llm_provider: LlmProvider::Google,
            openai_api_key: None,
            openai_base_url: None,
            openai_model: "gpt-5-nano".into(),
            openai_reasoning_effort: Some("low".into()),
            openai_text_verbosity: Some("low".into()),
            anthropic_api_key: None,
            anthropic_model: "claude-haiku-4-5-20251001".into(),
            google_api_key: None,
            google_model: "gemini-2.5-flash-lite".into(),
            max_response_tokens: 4096,
            allow_command_execution: true,
            command_confirmation: false,
            tool_confirmation: false,
            command_timeout: 30,
            allow_shell_operators: true,
            history_enabled: true,
            history_dir: hashcli_home().join("history"),
            max_history_size: 1000,
            history_retention_days: 30,
            rich_output: true,
            streaming: false,
            show_debug: false,
            log_level: LogLevel::Info,
            sandbox_commands: false,
            allowed_commands: None,
            blocked_commands: vec!["rm -rf".into(), "sudo".into(), "su".into()],
        }
    }
}

impl HashConfig {
    pub fn current_model(&self) -> &str {
        match self.llm_provider {
            LlmProvider::Openai => &self.openai_model,
            LlmProvider::Anthropic => &self.anthropic_model,
            LlmProvider::Google => &self.google_model,
        }
    }

    pub fn current_api_key(&self) -> Option<&str> {
        let key = match self.llm_provider {
            LlmProvider::Openai => &self.openai_api_key,
            LlmProvider::Anthropic => &self.anthropic_api_key,
            LlmProvider::Google => &self.google_api_key,
        };
        key.as_deref()
    }

    pub fn set_api_key(&mut self, provider: LlmProvider, key: Option<String>) {
        match provider {
            LlmProvider::Openai => self.openai_api_key = key,
            LlmProvider::Anthropic => self.anthropic_api_key = key,
            LlmProvider::Google => self.google_api_key = key,
        }
    }

    pub fn set_model(&mut self, provider: LlmProvider, model: String) {
        match provider {
            LlmProvider::Openai => self.openai_model = model,
            LlmProvider::Anthropic => self.anthropic_model = model,
            LlmProvider::Google => self.google_model = model,
        }
    }

    pub fn validate_current_setup(&self) -> bool {
        self.current_api_key().is_some_and(|k| !k.trim().is_empty())
    }

    /// Effective value of a config key, formatted for `/config get`.
    pub fn display_value(&self, key: &str) -> Option<String> {
        field_kind(key)?;
        let json = serde_json::to_value(self).ok()?;
        let value = json.get(key)?;
        Some(match value {
            serde_json::Value::Null => "(not set)".to_string(),
            serde_json::Value::String(s) => s.clone(),
            other => other.to_string(),
        })
    }

    /// Config as JSON with API keys removed (handed to executable plugins).
    pub fn to_plugin_json(&self) -> String {
        let mut json = serde_json::to_value(self).unwrap_or_default();
        if let Some(map) = json.as_object_mut() {
            map.retain(|key, _| !key.ends_with("_api_key"));
        }
        json.to_string()
    }
}

/// `~/.hashcli`
pub fn hashcli_home() -> PathBuf {
    home_dir().join(".hashcli")
}

pub fn home_dir() -> PathBuf {
    dirs::home_dir().unwrap_or_else(|| PathBuf::from("."))
}

pub fn user_config_path() -> PathBuf {
    hashcli_home().join("config.toml")
}

/// Configuration file paths in priority order (highest first).
pub fn get_config_paths() -> Vec<PathBuf> {
    let mut paths = vec![user_config_path()];
    if cfg!(windows) {
        let program_data = std::env::var("ProgramData").unwrap_or_else(|_| "C:/ProgramData".into());
        paths.push(PathBuf::from(program_data).join("hashcli").join("config.toml"));
    } else {
        paths.push(PathBuf::from("/etc/hashcli/config.toml"));
    }
    paths
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum FieldKind {
    Bool,
    Int,
    Str,
    OptStr,
    StrList,
    OptStrList,
    Path,
    Enum(&'static [&'static str]),
}

const PROVIDERS: &[&str] = &["openai", "anthropic", "google"];
const LOG_LEVELS: &[&str] = &["debug", "info", "warning", "error"];

/// Registry of all config keys and their value types.
pub const FIELDS: &[(&str, FieldKind)] = &[
    ("llm_provider", FieldKind::Enum(PROVIDERS)),
    ("openai_api_key", FieldKind::OptStr),
    ("openai_base_url", FieldKind::OptStr),
    ("openai_model", FieldKind::Str),
    ("openai_reasoning_effort", FieldKind::OptStr),
    ("openai_text_verbosity", FieldKind::OptStr),
    ("anthropic_api_key", FieldKind::OptStr),
    ("anthropic_model", FieldKind::Str),
    ("google_api_key", FieldKind::OptStr),
    ("google_model", FieldKind::Str),
    ("max_response_tokens", FieldKind::Int),
    ("allow_command_execution", FieldKind::Bool),
    ("command_confirmation", FieldKind::Bool),
    ("tool_confirmation", FieldKind::Bool),
    ("command_timeout", FieldKind::Int),
    ("allow_shell_operators", FieldKind::Bool),
    ("history_enabled", FieldKind::Bool),
    ("history_dir", FieldKind::Path),
    ("max_history_size", FieldKind::Int),
    ("history_retention_days", FieldKind::Int),
    ("rich_output", FieldKind::Bool),
    ("streaming", FieldKind::Bool),
    ("show_debug", FieldKind::Bool),
    ("log_level", FieldKind::Enum(LOG_LEVELS)),
    ("sandbox_commands", FieldKind::Bool),
    ("allowed_commands", FieldKind::OptStrList),
    ("blocked_commands", FieldKind::StrList),
];

const LEGACY_CONFIRMATION_KEY: &str = "require_confirmation";

pub fn field_kind(key: &str) -> Option<FieldKind> {
    FIELDS.iter().find(|(name, _)| *name == key).map(|(_, kind)| *kind)
}

fn valid_keys() -> String {
    let mut keys: Vec<&str> = FIELDS.iter().map(|(name, _)| *name).collect();
    keys.sort_unstable();
    keys.join(", ")
}

fn parse_bool(value: &str) -> Option<bool> {
    match value.trim().to_lowercase().as_str() {
        "true" | "1" | "yes" | "on" => Some(true),
        "false" | "0" | "no" | "off" => Some(false),
        _ => None,
    }
}

fn parse_toml_value(literal: &str) -> Option<Value> {
    let table: toml::Table = format!("value = {literal}").parse().ok()?;
    table.get("value").cloned()
}

fn parse_string_value(value: &str) -> Result<String, ConfigError> {
    let bytes = value.as_bytes();
    if value.len() >= 2 && (bytes[0] == b'"' || bytes[0] == b'\'') && bytes[value.len() - 1] == bytes[0] {
        return match parse_toml_value(value) {
            Some(Value::String(s)) => Ok(s),
            _ => Err(ConfigError::new(format!("Invalid quoted string: {value}"))),
        };
    }
    Ok(value.to_string())
}

/// Parse a CLI-provided config value according to the field type (`/config set`).
pub fn parse_config_value(key: &str, raw_value: &str) -> Result<Value, ConfigError> {
    let Some(kind) = field_kind(key) else {
        return Err(ConfigError::new(format!(
            "Unknown config key: {key}. Valid keys: {}",
            valid_keys()
        )));
    };
    let value = raw_value.trim();
    match kind {
        FieldKind::Bool => parse_bool(value)
            .map(Value::Boolean)
            .ok_or_else(|| ConfigError::new(format!("{key} must be a boolean value."))),
        FieldKind::Int => value
            .parse::<i64>()
            .map(Value::Integer)
            .map_err(|_| ConfigError::new(format!("{key} must be an integer value."))),
        FieldKind::StrList | FieldKind::OptStrList => match parse_toml_value(value) {
            Some(Value::Array(items)) if items.iter().all(Value::is_str) => Ok(Value::Array(items)),
            _ => Err(ConfigError::new(format!(
                "{key} must be a TOML list literal, for example [\"foo\", \"bar\"]."
            ))),
        },
        FieldKind::Path => Ok(Value::String(value.to_string())),
        FieldKind::Enum(choices) => {
            let parsed = parse_string_value(value)?.to_lowercase();
            if choices.contains(&parsed.as_str()) {
                Ok(Value::String(parsed))
            } else {
                Err(ConfigError::new(format!(
                    "{key} must be one of: {}.",
                    choices.join(", ")
                )))
            }
        }
        FieldKind::Str | FieldKind::OptStr => parse_string_value(value).map(Value::String),
    }
}

/// Parse a `HASHCLI_*` environment value according to the field type.
fn parse_env_value(key: &str, kind: FieldKind, raw: &str) -> Result<Value, ConfigError> {
    let invalid = |expected: &str| ConfigError::new(format!("Invalid configuration: {key} must be {expected}"));
    match kind {
        FieldKind::Bool => parse_bool(raw).map(Value::Boolean).ok_or_else(|| invalid("a boolean")),
        FieldKind::Int => raw
            .trim()
            .parse::<i64>()
            .map(Value::Integer)
            .map_err(|_| invalid("an integer")),
        FieldKind::StrList | FieldKind::OptStrList => {
            if let Some(Value::Array(items)) = parse_toml_value(raw.trim())
                && items.iter().all(Value::is_str)
            {
                return Ok(Value::Array(items));
            }
            Ok(Value::Array(
                raw.split(',')
                    .map(str::trim)
                    .filter(|s| !s.is_empty())
                    .map(|s| Value::String(s.to_string()))
                    .collect(),
            ))
        }
        FieldKind::Enum(choices) => {
            let lowered = raw.trim().to_lowercase();
            if choices.contains(&lowered.as_str()) {
                Ok(Value::String(lowered))
            } else {
                Err(invalid(&format!("one of: {}", choices.join(", "))))
            }
        }
        FieldKind::Str | FieldKind::OptStr | FieldKind::Path => Ok(Value::String(raw.to_string())),
    }
}

/// Coerce a value read from a TOML file into the type the field expects.
fn coerce_file_value(key: &str, kind: FieldKind, value: Value) -> Result<Value, ConfigError> {
    let mismatch = || ConfigError::new(format!("Invalid configuration: {key} has an invalid value"));
    match (kind, value) {
        (FieldKind::Bool, Value::Boolean(b)) => Ok(Value::Boolean(b)),
        (FieldKind::Bool, Value::String(s)) => parse_bool(&s).map(Value::Boolean).ok_or_else(mismatch),
        (FieldKind::Bool, Value::Integer(i)) if i == 0 || i == 1 => Ok(Value::Boolean(i == 1)),
        (FieldKind::Int, Value::Integer(i)) => Ok(Value::Integer(i)),
        (FieldKind::Int, Value::String(s)) => s.trim().parse().map(Value::Integer).map_err(|_| mismatch()),
        (FieldKind::Str | FieldKind::OptStr | FieldKind::Path, Value::String(s)) => Ok(Value::String(s)),
        (FieldKind::Str | FieldKind::OptStr, Value::Integer(i)) => Ok(Value::String(i.to_string())),
        (FieldKind::Str | FieldKind::OptStr, Value::Float(f)) => Ok(Value::String(f.to_string())),
        (FieldKind::StrList | FieldKind::OptStrList, Value::Array(items)) if items.iter().all(Value::is_str) => {
            Ok(Value::Array(items))
        }
        (FieldKind::Enum(choices), Value::String(s)) => {
            let lowered = s.trim().to_lowercase();
            if choices.contains(&lowered.as_str()) {
                Ok(Value::String(lowered))
            } else {
                Err(ConfigError::new(format!(
                    "Invalid configuration: {key} must be one of: {}",
                    choices.join(", ")
                )))
            }
        }
        _ => Err(mismatch()),
    }
}

/// Map the legacy `require_confirmation` key onto the split confirmation keys.
/// Explicit new keys in the same source take precedence.
fn apply_legacy_confirmation(source: &mut toml::Table) {
    if let Some(legacy) = source.remove(LEGACY_CONFIRMATION_KEY) {
        for key in ["command_confirmation", "tool_confirmation"] {
            source.entry(key.to_string()).or_insert_with(|| legacy.clone());
        }
    }
}

/// Load a TOML config file. Missing files yield an empty table.
pub fn load_config_file(path: &Path) -> Result<toml::Table, ConfigError> {
    if !path.exists() {
        return Ok(toml::Table::new());
    }
    let text = std::fs::read_to_string(path)
        .map_err(|e| ConfigError::new(format!("Invalid configuration file {}: {e}", path.display())))?;
    text.parse::<toml::Table>().map_err(|e| {
        ConfigError::new(format!(
            "Invalid configuration file {}: {}",
            path.display(),
            e.message()
        ))
    })
}

/// Read `HASHCLI_*` environment variables for known config keys.
pub fn load_environment_variables() -> Result<toml::Table, ConfigError> {
    let mut table = toml::Table::new();
    for (name, raw) in std::env::vars() {
        let Some(suffix) = name.strip_prefix("HASHCLI_") else {
            continue;
        };
        let key = suffix.to_lowercase();
        if key == LEGACY_CONFIRMATION_KEY {
            let value = parse_bool(&raw)
                .ok_or_else(|| ConfigError::new(format!("Invalid configuration: {key} must be a boolean")))?;
            table.insert(key, Value::Boolean(value));
            continue;
        }
        if let Some(kind) = field_kind(&key) {
            let value = parse_env_value(&key, kind, &raw)?;
            table.insert(key, value);
        }
    }
    apply_legacy_confirmation(&mut table);
    Ok(table)
}

fn infer_provider_from_model(model: &str) -> Option<LlmProvider> {
    let lowered = model.to_lowercase();
    if lowered.contains("gpt") || lowered.contains("openai") {
        Some(LlmProvider::Openai)
    } else if lowered.contains("claude") || lowered.contains("anthropic") {
        Some(LlmProvider::Anthropic)
    } else if lowered.contains("gemini") || lowered.contains("google") {
        Some(LlmProvider::Google)
    } else {
        None
    }
}

/// Expand a leading `~` to the home directory.
pub fn expand_home(path: &Path) -> PathBuf {
    let text = path.to_string_lossy();
    if text == "~" {
        home_dir()
    } else if let Some(rest) = text.strip_prefix("~/").or_else(|| text.strip_prefix("~\\")) {
        home_dir().join(rest)
    } else {
        path.to_path_buf()
    }
}

fn nonempty_env(name: &str) -> Option<String> {
    let value = std::env::var(name).ok()?;
    let trimmed = value.trim();
    (!trimmed.is_empty()).then(|| trimmed.to_string())
}

fn build_config(merged: toml::Table) -> Result<HashConfig, ConfigError> {
    let mut coerced = toml::Table::new();
    for (key, value) in merged {
        if let Some(kind) = field_kind(&key) {
            coerced.insert(key.clone(), coerce_file_value(&key, kind, value)?);
        }
    }
    let mut config: HashConfig = Value::Table(coerced)
        .try_into()
        .map_err(|e: toml::de::Error| ConfigError::new(format!("Invalid configuration: {}", e.message())))?;

    if config.max_response_tokens <= 0 {
        return Err(ConfigError::new(
            "Invalid configuration: max_response_tokens must be positive",
        ));
    }
    for key in [
        &mut config.openai_api_key,
        &mut config.anthropic_api_key,
        &mut config.google_api_key,
    ] {
        if let Some(value) = key.as_mut() {
            *value = value.trim().to_string();
        }
    }
    config.history_dir = expand_home(&config.history_dir);
    Ok(config)
}

/// Load configuration from all sources.
pub fn load_configuration(
    config_file: Option<&str>,
    debug: bool,
    model_override: Option<&str>,
) -> Result<HashConfig, ConfigError> {
    let mut paths = get_config_paths();
    if let Some(file) = config_file {
        paths.insert(0, expand_home(Path::new(file)));
    }

    let mut merged = toml::Table::new();
    for path in paths.iter().rev() {
        let mut file_config = load_config_file(path).inspect_err(|e| eprintln!("Warning: {e}"))?;
        apply_legacy_confirmation(&mut file_config);
        merged.extend(file_config);
    }
    merged.extend(load_environment_variables()?);

    if debug {
        merged.insert("show_debug".into(), Value::Boolean(true));
        merged.insert("log_level".into(), Value::String("debug".into()));
    }

    if let Some(model) = model_override
        && let Some(provider) = infer_provider_from_model(model)
    {
        merged.insert("llm_provider".into(), Value::String(provider.as_str().into()));
        let model_key = format!("{}_model", provider.as_str());
        merged.insert(model_key, Value::String(model.to_string()));
    }

    let mut config = build_config(merged).inspect_err(|e| eprintln!("Warning: {e}"))?;

    // Standard provider env vars apply unless the HASHCLI_-prefixed variable is set.
    if std::env::var_os("HASHCLI_OPENAI_API_KEY").is_none()
        && let Some(key) = nonempty_env("OPENAI_API_KEY")
    {
        config.openai_api_key = Some(key);
    }
    if std::env::var_os("HASHCLI_ANTHROPIC_API_KEY").is_none()
        && let Some(key) = nonempty_env("ANTHROPIC_API_KEY")
    {
        config.anthropic_api_key = Some(key);
    }
    if std::env::var_os("HASHCLI_GOOGLE_API_KEY").is_none()
        && let Some(key) = nonempty_env("GEMINI_API_KEY").or_else(|| nonempty_env("GOOGLE_API_KEY"))
    {
        config.google_api_key = Some(key);
    }

    Ok(config)
}

fn to_edit_value(value: &Value) -> toml_edit::Value {
    match value {
        Value::String(s) => s.as_str().into(),
        Value::Integer(i) => (*i).into(),
        Value::Float(f) => (*f).into(),
        Value::Boolean(b) => (*b).into(),
        Value::Datetime(d) => d.to_string().parse().unwrap_or_else(|_| d.to_string().into()),
        Value::Array(items) => {
            let mut array = toml_edit::Array::new();
            for item in items {
                array.push(to_edit_value(item));
            }
            toml_edit::Value::Array(array)
        }
        Value::Table(table) => {
            let mut inline = toml_edit::InlineTable::new();
            for (k, v) in table {
                inline.insert(k, to_edit_value(v));
            }
            toml_edit::Value::InlineTable(inline)
        }
    }
}

fn read_document(path: &Path) -> anyhow::Result<toml_edit::DocumentMut> {
    if !path.exists() {
        return Ok(toml_edit::DocumentMut::new());
    }
    Ok(std::fs::read_to_string(path)?.parse()?)
}

/// Update top-level keys in a TOML config file, preserving comments and other settings.
pub fn update_config_values(updates: &BTreeMap<String, Value>, config_path: Option<&Path>) -> anyhow::Result<()> {
    let path = config_path.map(Path::to_path_buf).unwrap_or_else(user_config_path);
    if let Some(parent) = path.parent() {
        std::fs::create_dir_all(parent)?;
    }
    let mut doc = read_document(&path)?;
    for (key, value) in updates {
        let mut new_value = to_edit_value(value);
        let table = doc.as_table_mut();
        match table.get_mut(key) {
            // Replace in place so the key's leading comments and inline comment survive.
            Some(item) if item.is_value() => {
                if let Some(existing) = item.as_value() {
                    *new_value.decor_mut() = existing.decor().clone();
                }
                *item = toml_edit::Item::Value(new_value);
            }
            _ => {
                table.insert(key, toml_edit::Item::Value(new_value));
            }
        }
    }
    std::fs::write(&path, doc.to_string())?;
    Ok(())
}

/// Remove top-level keys from a TOML config file, preserving comments and other settings.
pub fn remove_config_keys(keys: &[&str], config_path: Option<&Path>) -> anyhow::Result<()> {
    let path = config_path.map(Path::to_path_buf).unwrap_or_else(user_config_path);
    if !path.exists() {
        return Ok(());
    }
    let mut doc = read_document(&path)?;
    for key in keys {
        remove_top_level_key(&mut doc, key);
    }
    std::fs::write(&path, doc.to_string())?;
    Ok(())
}

fn raw_prefix(decor: &toml_edit::Decor) -> String {
    decor.prefix().and_then(|p| p.as_str()).unwrap_or("").to_string()
}

/// Remove a top-level key, moving comments attached above it onto the following item.
fn remove_top_level_key(doc: &mut toml_edit::DocumentMut, key: &str) {
    let table = doc.as_table_mut();
    let keys: Vec<String> = table.iter().map(|(k, _)| k.to_string()).collect();
    let Some(index) = keys.iter().position(|k| k == key) else {
        return;
    };
    let carried = table.key(key).map(|k| raw_prefix(k.leaf_decor())).unwrap_or_default();
    table.remove(key);
    if !carried.contains('#') {
        return;
    }

    let next_value_key = keys[index + 1..]
        .iter()
        .find(|k| table.get(k).is_some_and(toml_edit::Item::is_value))
        .cloned();
    if let Some(next) = next_value_key
        && let Some(mut next_key) = table.key_mut(&next)
    {
        let decor = next_key.leaf_decor_mut();
        let existing = raw_prefix(decor);
        decor.set_prefix(format!("{carried}{existing}"));
        return;
    }

    let first_table = table
        .iter_mut()
        .filter_map(|(_, item)| item.as_table_mut())
        .filter(|t| !t.is_implicit())
        .min_by_key(|t| t.position().unwrap_or(isize::MAX));
    if let Some(sub) = first_table {
        let existing = raw_prefix(sub.decor());
        sub.decor_mut().set_prefix(format!("{carried}{existing}"));
        return;
    }

    let trailing = doc.trailing().as_str().unwrap_or("").to_string();
    doc.set_trailing(format!("{carried}{trailing}"));
}

pub fn validate_api_setup(config: &HashConfig) -> Result<(), ConfigError> {
    if config.validate_current_setup() {
        return Ok(());
    }
    let provider = config.llm_provider.as_str();
    Err(ConfigError::new(format!(
        "No API key configured for {provider}. Set {}_API_KEY environment variable or add to config file. \
         Run \"hi --config\" to set this up interactively.",
        provider.to_uppercase()
    )))
}

pub fn get_model_options(provider: LlmProvider) -> &'static [&'static str] {
    match provider {
        LlmProvider::Openai => &["gpt-5.2", "gpt-5.1-codex-mini", "gpt-5-mini", "gpt-5-nano"],
        LlmProvider::Anthropic => &[
            "claude-haiku-4-5-20251001",
            "claude-sonnet-4-6",
            "claude-3-haiku-20240307",
        ],
        LlmProvider::Google => &[
            "gemini-3-pro-preview",
            "gemini-3-flash-preview",
            "gemini-3.1-flash-lite-preview",
            "gemini-2.5-pro",
            "gemini-2.5-flash",
        ],
    }
}

pub fn default_model(provider: LlmProvider) -> &'static str {
    match provider {
        LlmProvider::Openai => "gpt-5-nano",
        LlmProvider::Anthropic => "claude-haiku-4-5-20251001",
        LlmProvider::Google => "gemini-3.1-flash-lite-preview",
    }
}

#[cfg(test)]
pub(crate) mod tests {
    use super::*;
    use std::sync::{Mutex, MutexGuard};

    /// Serializes tests that mutate process environment variables.
    pub(crate) static ENV_LOCK: Mutex<()> = Mutex::new(());

    /// Holds the env lock, isolates HOME, and clears hashcli-related variables.
    pub(crate) struct EnvGuard {
        _lock: MutexGuard<'static, ()>,
        saved: Vec<(String, Option<String>)>,
        pub home: tempfile::TempDir,
    }

    const MANAGED_VARS: &[&str] = &[
        "HOME",
        "USERPROFILE",
        "OPENAI_API_KEY",
        "ANTHROPIC_API_KEY",
        "GOOGLE_API_KEY",
        "GEMINI_API_KEY",
        "TMUX",
        "STY",
        "TERM_SESSION_ID",
        "WT_SESSION",
        "TERM_PROGRAM",
        "XDG_CACHE_HOME",
        "TLDR_PAGES_SOURCE_LOCATION",
        "TLDR_CACHE_ENABLED",
        "SHELL",
        "VISUAL",
        "EDITOR",
    ];

    impl EnvGuard {
        pub(crate) fn new() -> Self {
            let lock = ENV_LOCK.lock().unwrap_or_else(|e| e.into_inner());
            let mut names: Vec<String> = MANAGED_VARS.iter().map(|s| s.to_string()).collect();
            names.extend(std::env::vars().map(|(k, _)| k).filter(|k| k.starts_with("HASHCLI_")));
            let saved = names.iter().map(|n| (n.clone(), std::env::var(n).ok())).collect();
            let home = tempfile::tempdir().unwrap();
            unsafe {
                for name in &names {
                    std::env::remove_var(name);
                }
                std::env::set_var("HOME", home.path());
                std::env::set_var("USERPROFILE", home.path());
            }
            EnvGuard {
                _lock: lock,
                saved,
                home,
            }
        }

        pub(crate) fn set(&self, key: &str, value: &str) {
            unsafe { std::env::set_var(key, value) };
        }
    }

    impl Drop for EnvGuard {
        fn drop(&mut self) {
            let current: Vec<String> = std::env::vars()
                .map(|(k, _)| k)
                .filter(|k| k.starts_with("HASHCLI_"))
                .collect();
            unsafe {
                for name in current {
                    std::env::remove_var(name);
                }
                for (name, value) in &self.saved {
                    match value {
                        Some(v) => std::env::set_var(name, v),
                        None => std::env::remove_var(name),
                    }
                }
            }
        }
    }

    #[test]
    fn field_registry_matches_struct() {
        let json = serde_json::to_value(HashConfig::default()).unwrap();
        let mut struct_keys: Vec<&str> = json.as_object().unwrap().keys().map(String::as_str).collect();
        let mut registry_keys: Vec<&str> = FIELDS.iter().map(|(k, _)| *k).collect();
        struct_keys.sort_unstable();
        registry_keys.sort_unstable();
        assert_eq!(struct_keys, registry_keys);
    }

    #[test]
    fn default_configuration() {
        let config = HashConfig::default();
        assert_eq!(config.llm_provider, LlmProvider::Google);
        assert_eq!(config.openai_model, "gpt-5-nano");
        assert_eq!(config.openai_api_key, None);
        assert!(config.allow_command_execution);
        assert!(!config.command_confirmation);
        assert!(!config.tool_confirmation);
        assert!(config.allow_shell_operators);
        assert!(config.history_enabled);
        assert_eq!(config.command_timeout, 30);
        assert_eq!(config.max_response_tokens, 4096);
        assert_eq!(config.blocked_commands, vec!["rm -rf", "sudo", "su"]);
    }

    #[test]
    fn current_model_and_key_follow_provider() {
        let mut config = HashConfig {
            openai_api_key: Some("o".into()),
            anthropic_api_key: Some("a".into()),
            ..Default::default()
        };
        config.llm_provider = LlmProvider::Openai;
        assert_eq!(config.current_model(), "gpt-5-nano");
        assert_eq!(config.current_api_key(), Some("o"));
        config.llm_provider = LlmProvider::Anthropic;
        assert_eq!(config.current_model(), "claude-haiku-4-5-20251001");
        assert_eq!(config.current_api_key(), Some("a"));
        config.llm_provider = LlmProvider::Google;
        assert_eq!(config.current_api_key(), None);
        assert!(!config.validate_current_setup());
        config.google_api_key = Some("   ".into());
        assert!(!config.validate_current_setup());
        config.google_api_key = Some("g".into());
        assert!(config.validate_current_setup());
    }

    #[test]
    fn environment_variables_are_type_directed() {
        let env = EnvGuard::new();
        env.set("HASHCLI_STREAMING", "yes");
        env.set("HASHCLI_COMMAND_TIMEOUT", "1");
        env.set("HASHCLI_OPENAI_MODEL", "gpt-5-mini");
        env.set("HASHCLI_LLM_PROVIDER", "OpenAI");
        env.set("HASHCLI_BLOCKED_COMMANDS", "rm,shutdown");
        env.set("HASHCLI_SESSION_ID", "not-a-config-key");
        let table = load_environment_variables().unwrap();
        assert_eq!(table.get("streaming"), Some(&Value::Boolean(true)));
        assert_eq!(table.get("command_timeout"), Some(&Value::Integer(1)));
        assert_eq!(table.get("openai_model"), Some(&Value::String("gpt-5-mini".into())));
        assert_eq!(table.get("llm_provider"), Some(&Value::String("openai".into())));
        assert!(table.get("session_id").is_none());

        let config = load_configuration(None, false, None).unwrap();
        assert!(config.streaming);
        assert_eq!(config.command_timeout, 1);
        assert_eq!(config.llm_provider, LlmProvider::Openai);
        assert_eq!(config.blocked_commands, vec!["rm", "shutdown"]);
    }

    #[test]
    fn invalid_environment_value_is_an_error() {
        let env = EnvGuard::new();
        env.set("HASHCLI_COMMAND_TIMEOUT", "soon");
        let err = load_configuration(None, false, None).unwrap_err();
        assert!(err.to_string().contains("command_timeout"));
    }

    #[test]
    fn legacy_require_confirmation_env() {
        let env = EnvGuard::new();
        env.set("HASHCLI_REQUIRE_CONFIRMATION", "true");
        env.set("HASHCLI_TOOL_CONFIRMATION", "false");
        let config = load_configuration(None, false, None).unwrap();
        assert!(config.command_confirmation);
        assert!(!config.tool_confirmation);
    }

    #[test]
    fn legacy_require_confirmation_file() {
        let env = EnvGuard::new();
        let path = env.home.path().join("custom.toml");
        std::fs::write(&path, "require_confirmation = true\n").unwrap();
        let config = load_configuration(Some(path.to_str().unwrap()), false, None).unwrap();
        assert!(config.command_confirmation);
        assert!(config.tool_confirmation);
    }

    #[test]
    fn overrides_debug_and_model() {
        let _env = EnvGuard::new();
        let config = load_configuration(None, true, Some("claude-sonnet-4-6")).unwrap();
        assert!(config.show_debug);
        assert_eq!(config.log_level, LogLevel::Debug);
        assert_eq!(config.llm_provider, LlmProvider::Anthropic);
        assert_eq!(config.anthropic_model, "claude-sonnet-4-6");

        let config = load_configuration(None, false, Some("mystery-model")).unwrap();
        assert_eq!(config.llm_provider, LlmProvider::Google);
    }

    #[test]
    fn file_priority_and_env_override() {
        let env = EnvGuard::new();
        let user = user_config_path();
        std::fs::create_dir_all(user.parent().unwrap()).unwrap();
        std::fs::write(
            &user,
            "openai_model = \"from-user\"\nstreaming = true\ncommand_timeout = 5\n",
        )
        .unwrap();
        let custom = env.home.path().join("custom.toml");
        std::fs::write(&custom, "openai_model = \"from-custom\"\n").unwrap();
        env.set("HASHCLI_COMMAND_TIMEOUT", "9");
        let config = load_configuration(Some(custom.to_str().unwrap()), false, None).unwrap();
        assert_eq!(config.openai_model, "from-custom");
        assert!(config.streaming);
        assert_eq!(config.command_timeout, 9);
    }

    #[test]
    fn invalid_file_is_an_error() {
        let env = EnvGuard::new();
        let custom = env.home.path().join("broken.toml");
        std::fs::write(&custom, "this is = = not toml").unwrap();
        let err = load_configuration(Some(custom.to_str().unwrap()), false, None).unwrap_err();
        assert!(err.to_string().starts_with("Invalid configuration file"));
    }

    #[test]
    fn non_positive_tokens_rejected() {
        let env = EnvGuard::new();
        env.set("HASHCLI_MAX_RESPONSE_TOKENS", "0");
        assert!(load_configuration(None, false, None).is_err());
    }

    #[test]
    fn standard_api_keys_from_env() {
        let env = EnvGuard::new();
        env.set("OPENAI_API_KEY", " sk-openai ");
        env.set("ANTHROPIC_API_KEY", "sk-ant");
        env.set("GOOGLE_API_KEY", "g-key");
        env.set("GEMINI_API_KEY", "gemini-key");
        let config = load_configuration(None, false, None).unwrap();
        assert_eq!(config.openai_api_key.as_deref(), Some("sk-openai"));
        assert_eq!(config.anthropic_api_key.as_deref(), Some("sk-ant"));
        assert_eq!(config.google_api_key.as_deref(), Some("gemini-key"));
    }

    #[test]
    fn hashcli_api_key_beats_standard_env() {
        let env = EnvGuard::new();
        let user = user_config_path();
        std::fs::create_dir_all(user.parent().unwrap()).unwrap();
        std::fs::write(
            &user,
            "openai_api_key = \"from-file\"\nanthropic_api_key = \"file-ant\"\n",
        )
        .unwrap();
        env.set("OPENAI_API_KEY", "from-env");
        env.set("HASHCLI_ANTHROPIC_API_KEY", " hashcli-ant ");
        env.set("ANTHROPIC_API_KEY", "standard-ant");
        let config = load_configuration(None, false, None).unwrap();
        assert_eq!(config.openai_api_key.as_deref(), Some("from-env"));
        assert_eq!(config.anthropic_api_key.as_deref(), Some("hashcli-ant"));
    }

    #[test]
    fn history_dir_expands_home() {
        let env = EnvGuard::new();
        env.set("HASHCLI_HISTORY_DIR", "~/hist");
        let config = load_configuration(None, false, None).unwrap();
        assert_eq!(config.history_dir, env.home.path().join("hist"));
    }

    #[test]
    fn update_config_values_preserves_comments() {
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join("config.toml");
        std::fs::write(
            &path,
            "# top comment\nllm_provider = \"google\" # provider note\nstreaming = false\n\n[extra]\nkeep = 1\n",
        )
        .unwrap();
        let mut updates = BTreeMap::new();
        updates.insert("streaming".to_string(), Value::Boolean(true));
        updates.insert("llm_provider".to_string(), Value::String("openai".into()));
        updates.insert("openai_model".to_string(), Value::String("gpt-5-mini".into()));
        update_config_values(&updates, Some(&path)).unwrap();
        let text = std::fs::read_to_string(&path).unwrap();
        assert!(text.contains("# top comment"));
        assert!(text.contains("llm_provider = \"openai\" # provider note"));
        assert!(text.contains("streaming = true"));
        assert!(text.contains("[extra]\nkeep = 1"));
        let parsed: toml::Table = text.parse().unwrap();
        assert_eq!(parsed["openai_model"].as_str(), Some("gpt-5-mini"));
        assert!(parsed["extra"].as_table().unwrap().get("openai_model").is_none());
    }

    #[test]
    fn update_config_values_creates_file() {
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join("nested").join("config.toml");
        let mut updates = BTreeMap::new();
        updates.insert("streaming".to_string(), Value::Boolean(true));
        update_config_values(&updates, Some(&path)).unwrap();
        assert_eq!(std::fs::read_to_string(&path).unwrap().trim(), "streaming = true");
    }

    #[test]
    fn remove_config_keys_preserves_comments() {
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join("config.toml");
        std::fs::write(
            &path,
            "# note\nstreaming = true\nopenai_model = \"x\"\n# last\ncommand_timeout = 5\n\n[extra]\nkeep = 1\n",
        )
        .unwrap();
        remove_config_keys(&["streaming", "command_timeout"], Some(&path)).unwrap();
        let text = std::fs::read_to_string(&path).unwrap();
        assert!(text.starts_with("# note\nopenai_model = \"x\"\n"), "{text}");
        assert!(!text.contains("streaming"));
        assert!(!text.contains("command_timeout"));
        assert!(
            text.contains("# last\n\n[extra]") || text.contains("# last\n[extra]"),
            "{text}"
        );
        remove_config_keys(&["streaming"], Some(&dir.path().join("missing.toml"))).unwrap();
    }

    #[test]
    fn parse_config_value_by_field_type() {
        assert_eq!(parse_config_value("streaming", "yes").unwrap(), Value::Boolean(true));
        assert_eq!(parse_config_value("command_timeout", "45").unwrap(), Value::Integer(45));
        assert_eq!(
            parse_config_value("openai_model", "\"gpt-5\"").unwrap(),
            Value::String("gpt-5".into())
        );
        assert_eq!(
            parse_config_value("openai_model", "gpt-5").unwrap(),
            Value::String("gpt-5".into())
        );
        assert_eq!(
            parse_config_value("llm_provider", "Anthropic").unwrap(),
            Value::String("anthropic".into())
        );
        assert_eq!(
            parse_config_value("blocked_commands", "[\"rm\", \"sudo\"]").unwrap(),
            Value::Array(vec![Value::String("rm".into()), Value::String("sudo".into())])
        );
        assert!(parse_config_value("streaming", "maybe").is_err());
        assert!(parse_config_value("command_timeout", "x").is_err());
        assert!(parse_config_value("llm_provider", "bogus").is_err());
        assert!(parse_config_value("blocked_commands", "rm").is_err());
        let err = parse_config_value("nope", "1").unwrap_err();
        assert!(err.to_string().starts_with("Unknown config key: nope. Valid keys:"));
    }

    #[test]
    fn display_value_formats() {
        let config = HashConfig::default();
        assert_eq!(config.display_value("streaming").as_deref(), Some("false"));
        assert_eq!(config.display_value("llm_provider").as_deref(), Some("google"));
        assert_eq!(config.display_value("openai_api_key").as_deref(), Some("(not set)"));
        assert_eq!(config.display_value("unknown"), None);
    }

    #[test]
    fn plugin_json_omits_api_keys() {
        let config = HashConfig {
            openai_api_key: Some("secret".into()),
            ..Default::default()
        };
        let json = config.to_plugin_json();
        assert!(!json.contains("secret"));
        assert!(!json.contains("openai_api_key"));
        assert!(json.contains("\"llm_provider\":\"google\""));
    }

    #[test]
    fn validate_api_setup_message() {
        let config = HashConfig::default();
        let err = validate_api_setup(&config).unwrap_err();
        assert!(
            err.to_string()
                .starts_with("No API key configured for google. Set GOOGLE_API_KEY")
        );
    }

    #[test]
    fn provider_parsing() {
        assert_eq!(LlmProvider::parse("OpenAI"), Some(LlmProvider::Openai));
        assert_eq!(LlmProvider::parse("bogus"), None);
        assert_eq!(LlmProvider::Anthropic.to_string(), "anthropic");
    }
}
