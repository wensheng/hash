//! Conversation session resolution for each invocation.

use sha2::{Digest, Sha256};

use crate::config::{ConfigError, HashConfig};
use crate::history::ConversationHistory;
use crate::ui;

const SESSION_ENV_MARKERS: &[&str] = &["TMUX", "STY", "TERM_SESSION_ID", "WT_SESSION", "TERM_PROGRAM"];

fn parent_pid() -> u32 {
    #[cfg(unix)]
    {
        std::os::unix::process::parent_id()
    }
    #[cfg(not(unix))]
    {
        0
    }
}

/// Best-effort TTY name across stdio streams.
fn active_tty() -> String {
    #[cfg(unix)]
    {
        use std::io::IsTerminal;
        use std::os::fd::AsFd;
        let stdin = std::io::stdin();
        let stdout = std::io::stdout();
        let stderr = std::io::stderr();
        let streams = [
            (stdin.is_terminal(), stdin.as_fd()),
            (stdout.is_terminal(), stdout.as_fd()),
            (stderr.is_terminal(), stderr.as_fd()),
        ];
        for (is_tty, fd) in streams {
            if is_tty && let Ok(name) = nix::unistd::ttyname(fd) {
                return name.to_string_lossy().into_owned();
            }
        }
    }
    "no-tty".to_string()
}

/// Stable fingerprint of the current shell session.
pub fn build_shell_scope_fingerprint() -> String {
    let mut parts = vec![parent_pid().to_string(), active_tty()];
    parts.extend(SESSION_ENV_MARKERS.iter().map(|k| std::env::var(k).unwrap_or_default()));
    parts.join("|")
}

pub fn shell_session_id(fingerprint: &str) -> String {
    let digest = Sha256::digest(fingerprint.as_bytes());
    let hex: String = digest.iter().map(|b| format!("{b:02x}")).collect();
    format!("shell-{}", &hex[..24])
}

/// Python-style `repr()` for a user-provided id.
fn repr(text: &str) -> String {
    format!("'{}'", text.replace('\\', "\\\\").replace('\'', "\\'"))
}

/// Resolve the conversation session id for this invocation.
pub fn resolve_conversation_session_id(
    new_session: bool,
    requested_session_id: Option<&str>,
    config: &HashConfig,
) -> Result<Option<String>, ConfigError> {
    if new_session && requested_session_id.is_some() {
        return Err(ConfigError::new("--session cannot be combined with --new-session."));
    }
    if new_session {
        return Ok(Some(uuid::Uuid::new_v4().to_string()));
    }
    if let Some(requested) = requested_session_id {
        if !config.history_enabled {
            return Err(ConfigError::new("--session requires history to be enabled."));
        }
        let history = ConversationHistory::new(&config.history_dir).map_err(|e| ConfigError::new(format!("{e:#}")))?;
        let to_config_error = |e: anyhow::Error| ConfigError::new(format!("{e:#}"));
        if let Some(resolved) = history.resolve_session_id(requested).map_err(to_config_error)? {
            return Ok(Some(resolved));
        }
        let matches = history.find_session_ids(requested, 10).map_err(to_config_error)?;
        if matches.len() > 1 {
            let list: Vec<String> = matches.iter().map(|m| format!("  {m}")).collect();
            return Err(ConfigError::new(format!(
                "Ambiguous session ID prefix {}. Matches:\n{}",
                repr(requested),
                list.join("\n")
            )));
        }
        return Err(ConfigError::new(format!("No session found for {}.", repr(requested))));
    }

    if let Ok(existing) = std::env::var("HASHCLI_SESSION_ID") {
        let existing = existing.trim();
        if !existing.is_empty() {
            return Ok(Some(existing.to_string()));
        }
    }
    if !ui::is_interactive() {
        return Ok(None);
    }
    Ok(Some(shell_session_id(&build_shell_scope_fingerprint())))
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::config::tests::EnvGuard;

    fn config(env: &EnvGuard) -> HashConfig {
        HashConfig {
            history_dir: env.home.path().join("history"),
            ..Default::default()
        }
    }

    #[test]
    fn fingerprint_contains_parent_and_tty() {
        let env = EnvGuard::new();
        env.set("TMUX", "/tmp/tmux-1/default,1,0");
        let fingerprint = build_shell_scope_fingerprint();
        let parts: Vec<&str> = fingerprint.split('|').collect();
        assert_eq!(parts.len(), 7);
        assert_eq!(parts[0], parent_pid().to_string());
        assert_eq!(parts[2], "/tmp/tmux-1/default,1,0");
    }

    #[test]
    fn shell_session_ids_are_stable() {
        let a = shell_session_id("123|/dev/pts/1||||");
        assert_eq!(a, shell_session_id("123|/dev/pts/1||||"));
        assert!(a.starts_with("shell-"));
        assert_eq!(a.len(), "shell-".len() + 24);
        assert_ne!(a, shell_session_id("124|/dev/pts/1||||"));
    }

    #[test]
    fn prefers_env_session_and_new_session() {
        let env = EnvGuard::new();
        let cfg = config(&env);
        env.set("HASHCLI_SESSION_ID", "from-env");
        assert_eq!(
            resolve_conversation_session_id(false, None, &cfg).unwrap().as_deref(),
            Some("from-env")
        );
        let fresh = resolve_conversation_session_id(true, None, &cfg).unwrap().unwrap();
        assert_ne!(fresh, "from-env");
        assert_eq!(fresh.len(), 36);
        assert!(resolve_conversation_session_id(true, Some("x"), &cfg).is_err());
    }

    #[test]
    fn resolves_requested_prefixes() {
        let env = EnvGuard::new();
        let cfg = config(&env);
        let history = ConversationHistory::new(&cfg.history_dir).unwrap();
        history.start_session(Some("abc-1")).unwrap();
        history.start_session(Some("abc-2")).unwrap();
        history.start_session(Some("xyz-1")).unwrap();
        assert_eq!(
            resolve_conversation_session_id(false, Some("xyz"), &cfg)
                .unwrap()
                .as_deref(),
            Some("xyz-1")
        );
        let err = resolve_conversation_session_id(false, Some("abc"), &cfg).unwrap_err();
        assert!(err.to_string().starts_with("Ambiguous session ID prefix 'abc'."));
        let err = resolve_conversation_session_id(false, Some("nope"), &cfg).unwrap_err();
        assert_eq!(err.to_string(), "No session found for 'nope'.");

        let disabled = HashConfig {
            history_enabled: false,
            ..cfg
        };
        assert!(resolve_conversation_session_id(false, Some("xyz"), &disabled).is_err());
    }
}
