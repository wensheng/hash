//! Shell command execution tool.

use std::process::Stdio;
use std::sync::LazyLock;
use std::time::Duration;

use async_trait::async_trait;
use regex::Regex;
use serde_json::Value;

use super::Tool;
use crate::config::HashConfig;
use crate::shlex;

pub struct ShellTool;

const DESTRUCTIVE_COMMAND_PATTERNS: &[&str] = &[
    r"\bkill(?:all)?\b",
    r"\bpkill\b",
    r"\bfuser\b[^\n\r]*\s-k\b",
    r"\blsof\b[^\n\r]*\|\s*xargs\b[^\n\r]*\bkill\b",
    r"\btaskkill\b",
    r"\bstop-process\b",
    r"\bremove-item\b",
    r"\brm\b",
    r"\bdel\b",
    r"\bmv\b",
    r"\bchmod\b",
    r"\bchown\b",
    r"\btruncate\b",
    r"\bmkfs\b",
    r"\bdd\b",
    r"\bshutdown\b",
    r"\breboot\b",
    r"\bpoweroff\b",
    r"\binit\s+[06]\b",
    r"\blaunchctl\s+(?:unload|remove|bootout)\b",
    r"\bsystemctl\s+(?:stop|restart|kill|disable|mask)\b",
    r"\bservice\s+\S+\s+(?:stop|restart)\b",
    r"\bdocker\s+(?:rm|rmi|stop|kill)\b",
    r"\bkubectl\s+delete\b",
    r"\bgit\s+reset\b",
    r"\bgit\s+clean\b",
    r"\bbrew\s+uninstall\b",
    r"\bapt(?:-get)?\s+remove\b",
    r"\byum\s+remove\b",
    r"\bdnf\s+remove\b",
    r"\bpacman\s+-R\b",
];

static DESTRUCTIVE_REGEXES: LazyLock<Vec<Regex>> = LazyLock::new(|| {
    DESTRUCTIVE_COMMAND_PATTERNS
        .iter()
        .map(|p| Regex::new(p).expect("valid destructive pattern"))
        .collect()
});

impl ShellTool {
    /// True when the command can stop processes or mutate the system.
    pub fn is_potentially_destructive_command(command: &str) -> bool {
        let normalized = command.trim().to_lowercase();
        if normalized.is_empty() {
            return false;
        }
        DESTRUCTIVE_REGEXES.iter().any(|re| re.is_match(&normalized))
    }

    /// Returns a rejection message when the command violates the security policy.
    pub fn validate_command_security(command: &str, config: &HashConfig) -> Option<String> {
        let command_lower = command.to_lowercase();
        for blocked in &config.blocked_commands {
            if contains_whole_words(&command_lower, &blocked.to_lowercase()) {
                return Some(format!("Blocked command detected: {blocked}"));
            }
        }

        if let Some(allowed) = config.allowed_commands.as_ref().filter(|a| !a.is_empty()) {
            for base in extract_base_commands(command) {
                if !allowed.contains(&base) {
                    return Some(format!("Command not in allowed list: {base}"));
                }
            }
        }

        let mut dangerous_patterns = vec![
            "$(",
            "`",
            "&&",
            "||",
            "rm -rf /",
            "sudo rm",
            "chmod 777",
            "curl | sh",
            "wget | sh",
        ];
        if !config.allow_shell_operators {
            dangerous_patterns.extend([";", "|"]);
        }
        for pattern in dangerous_patterns {
            if command_lower.contains(pattern) {
                return Some(format!("Potentially dangerous command pattern detected: {pattern}"));
            }
        }
        None
    }

    fn should_use_shell(command: &str, config: &HashConfig) -> bool {
        config.allow_shell_operators && (command.contains('|') || command.contains(';'))
    }

    async fn run_command(command: &str, config: &HashConfig, passthrough_output: bool) -> String {
        let mut cmd = if Self::should_use_shell(command, config) {
            let mut c = if cfg!(windows) {
                let mut c = tokio::process::Command::new("cmd");
                c.arg("/C");
                c
            } else {
                let mut c = tokio::process::Command::new("/bin/sh");
                c.arg("-c");
                c
            };
            c.arg(command);
            c
        } else {
            let parts = match shlex::split(command) {
                Ok(parts) => parts,
                Err(e) => return format!("Error executing command: {e}"),
            };
            let Some((program, args)) = parts.split_first() else {
                return "No command provided.".to_string();
            };
            let mut c = tokio::process::Command::new(program);
            c.args(args);
            c
        };
        cmd.kill_on_drop(true);

        let timeout = Duration::from_secs(config.command_timeout.max(0) as u64);
        let not_found = || {
            let first = shlex::split(command)
                .ok()
                .and_then(|p| p.into_iter().next())
                .unwrap_or_else(|| "unknown".to_string());
            format!("Command not found: {first}")
        };
        let spawn_error = |e: std::io::Error| {
            if e.kind() == std::io::ErrorKind::NotFound {
                not_found()
            } else {
                format!("Error executing command: {e}")
            }
        };

        if passthrough_output {
            let mut child = match cmd.spawn() {
                Ok(child) => child,
                Err(e) => return spawn_error(e),
            };
            return match tokio::time::timeout(timeout, child.wait()).await {
                Err(_) => {
                    let _ = child.kill().await;
                    format!("Command timed out after {} seconds", config.command_timeout)
                }
                Ok(Err(e)) => format!("Error executing command: {e}"),
                Ok(Ok(status)) => {
                    let code = exit_code(&status);
                    if code != 0 {
                        format!("Exit code: {code}")
                    } else {
                        String::new()
                    }
                }
            };
        }

        cmd.stdout(Stdio::piped()).stderr(Stdio::piped());
        let child = match cmd.spawn() {
            Ok(child) => child,
            Err(e) => return spawn_error(e),
        };
        let output = match tokio::time::timeout(timeout, child.wait_with_output()).await {
            Err(_) => return format!("Command timed out after {} seconds", config.command_timeout),
            Ok(Err(e)) => return format!("Error executing command: {e}"),
            Ok(Ok(output)) => output,
        };

        let code = exit_code(&output.status);
        let stdout = String::from_utf8_lossy(&output.stdout);
        let stderr = String::from_utf8_lossy(&output.stderr);
        let mut result = String::new();
        if !stdout.is_empty() {
            result.push_str(&stdout);
        }
        if !stderr.is_empty() {
            if !result.is_empty() {
                result.push_str("\n\n");
            }
            result.push_str("stderr:\n");
            result.push_str(&stderr);
        }
        if code != 0 {
            if !result.is_empty() {
                result.push_str("\n\n");
            }
            result.push_str(&format!("Exit code: {code}"));
        }
        if result.is_empty() {
            result = format!("Command completed successfully (exit code: {code})");
        }
        result.trim().to_string()
    }
}

fn exit_code(status: &std::process::ExitStatus) -> i32 {
    if let Some(code) = status.code() {
        return code;
    }
    #[cfg(unix)]
    {
        use std::os::unix::process::ExitStatusExt;
        if let Some(signal) = status.signal() {
            return -signal;
        }
    }
    -1
}

/// True when `needle` occurs in `haystack` without being part of a larger word.
///
/// Word boundaries are only required at edges where the needle itself starts or ends
/// with a word character, so `"su"` matches `su -` but not `results.txt`, while
/// `"rm -rf /"` still matches `rm -rf /home`. Blank needles never match.
pub fn contains_whole_words(haystack: &str, needle: &str) -> bool {
    let needle = needle.trim();
    let (Some(first), Some(last)) = (needle.chars().next(), needle.chars().last()) else {
        return false;
    };
    let is_word = |c: char| c.is_alphanumeric() || c == '_';
    let pattern = format!(
        "{}{}{}",
        if is_word(first) { r"\b" } else { "" },
        regex::escape(needle),
        if is_word(last) { r"\b" } else { "" },
    );
    Regex::new(&pattern).is_ok_and(|re| re.is_match(haystack))
}

/// Split a command string on unquoted `|` and `;`.
pub fn split_command_chain(command: &str) -> Vec<String> {
    let mut segments = Vec::new();
    let mut current = String::new();
    let mut in_single = false;
    let mut in_double = false;
    let mut escaped = false;

    let flush = |current: &mut String, segments: &mut Vec<String>| {
        let segment = current.trim();
        if !segment.is_empty() {
            segments.push(segment.to_string());
        }
        current.clear();
    };

    for ch in command.chars() {
        if escaped {
            current.push(ch);
            escaped = false;
            continue;
        }
        if ch == '\\' && !in_single {
            escaped = true;
            current.push(ch);
            continue;
        }
        if ch == '\'' && !in_double {
            in_single = !in_single;
            current.push(ch);
            continue;
        }
        if ch == '"' && !in_single {
            in_double = !in_double;
            current.push(ch);
            continue;
        }
        if !in_single && !in_double && (ch == '|' || ch == ';') {
            flush(&mut current, &mut segments);
            continue;
        }
        current.push(ch);
    }
    flush(&mut current, &mut segments);
    segments
}

/// First word of each command in a `|`/`;` chain.
pub fn extract_base_commands(command: &str) -> Vec<String> {
    split_command_chain(command)
        .iter()
        .filter_map(|segment| shlex::split(segment).ok()?.into_iter().next())
        .collect()
}

#[async_trait(?Send)]
impl Tool for ShellTool {
    fn name(&self) -> &'static str {
        "execute_shell_command"
    }

    async fn execute(&self, arguments: &Value, config: &HashConfig) -> anyhow::Result<String> {
        if !config.allow_command_execution {
            return Ok("Command execution is disabled in configuration.".to_string());
        }
        let command = arguments.get("command").and_then(Value::as_str).unwrap_or("");
        let passthrough = arguments
            .get("passthrough_output")
            .is_some_and(|v| v.as_bool().unwrap_or(!v.is_null()));
        if command.is_empty() {
            return Ok("No command provided.".to_string());
        }
        if let Some(rejection) = Self::validate_command_security(command, config) {
            return Ok(rejection);
        }
        Ok(Self::run_command(command, config, passthrough).await)
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    fn config() -> HashConfig {
        HashConfig::default()
    }

    #[test]
    fn destructive_detection() {
        for cmd in [
            "kill -9 1234",
            "killall node",
            "rm file.txt",
            "fuser -n tcp 8080 -k",
            "lsof -ti:3000 | xargs kill",
            "docker rm abc",
            "git reset --hard",
            "systemctl restart nginx",
            "MV a b",
        ] {
            assert!(ShellTool::is_potentially_destructive_command(cmd), "{cmd}");
        }
        for cmd in ["ls -la", "git status", "docker ps", "", "   ", "echo remove"] {
            assert!(!ShellTool::is_potentially_destructive_command(cmd), "{cmd}");
        }
    }

    #[test]
    fn security_blocks_configured_and_dangerous_patterns() {
        let cfg = config();
        assert_eq!(
            ShellTool::validate_command_security("sudo ls", &cfg).unwrap(),
            "Blocked command detected: sudo"
        );
        assert_eq!(
            ShellTool::validate_command_security("echo $(whoami)", &cfg).unwrap(),
            "Potentially dangerous command pattern detected: $("
        );
        assert!(
            ShellTool::validate_command_security("ls && pwd", &cfg)
                .unwrap()
                .contains("&&")
        );
        assert!(ShellTool::validate_command_security("ls | wc -l", &cfg).is_none());

        let strict = HashConfig {
            allow_shell_operators: false,
            ..config()
        };
        assert!(
            ShellTool::validate_command_security("ls | wc -l", &strict)
                .unwrap()
                .ends_with(": |")
        );
    }

    #[test]
    fn blocked_commands_match_whole_words() {
        let cfg = config();
        for allowed in [
            "cat results.txt",
            "head summary.md",
            "ls /nonexistent/such-dir",
            "rm -rfv build",
            "echo pseudo",
            "visudo -c",
        ] {
            assert!(
                ShellTool::validate_command_security(allowed, &cfg).is_none(),
                "{allowed}"
            );
        }
        for (blocked, entry) in [
            ("su -", "su"),
            ("SUDO ls", "sudo"),
            ("/usr/bin/sudo ls", "sudo"),
            ("rm -rf build", "rm -rf"),
            ("ls | sudo tee x", "sudo"),
        ] {
            assert_eq!(
                ShellTool::validate_command_security(blocked, &cfg).unwrap(),
                format!("Blocked command detected: {entry}"),
                "{blocked}"
            );
        }

        let custom = HashConfig {
            blocked_commands: vec!["rm -rf /".into(), "dd if=".into(), "mkfs".into(), "  ".into()],
            ..config()
        };
        assert!(ShellTool::validate_command_security("dd if=/dev/zero of=x", &custom).is_some());
        assert!(ShellTool::validate_command_security("mkfs.ext4 /dev/sdb1", &custom).is_some());
        assert!(ShellTool::validate_command_security("sudo ls", &custom).is_none());
        assert!(ShellTool::validate_command_security("add if=x", &custom).is_none());
        assert!(contains_whole_words("rm -rf /home", "rm -rf /"));
        assert!(!contains_whole_words("anything", ""));
    }

    #[test]
    fn security_allowlist_checks_every_segment() {
        let cfg = HashConfig {
            allowed_commands: Some(vec!["ls".into(), "wc".into()]),
            ..config()
        };
        assert!(ShellTool::validate_command_security("ls | wc -l", &cfg).is_none());
        assert_eq!(
            ShellTool::validate_command_security("ls | grep x", &cfg).unwrap(),
            "Command not in allowed list: grep"
        );
    }

    #[test]
    fn splits_command_chains_respecting_quotes() {
        assert_eq!(split_command_chain("ls | wc -l; pwd"), vec!["ls", "wc -l", "pwd"]);
        assert_eq!(split_command_chain("echo 'a|b' | cat"), vec!["echo 'a|b'", "cat"]);
        assert_eq!(split_command_chain(r"echo a\|b"), vec![r"echo a\|b"]);
        assert_eq!(extract_base_commands("grep \"x;y\" f | sort"), vec!["grep", "sort"]);
    }

    #[tokio::test]
    async fn execute_reports_disabled_and_empty() {
        let disabled = HashConfig {
            allow_command_execution: false,
            ..config()
        };
        let tool = ShellTool;
        assert_eq!(
            tool.execute(&json!({"command": "ls"}), &disabled).await.unwrap(),
            "Command execution is disabled in configuration."
        );
        assert_eq!(
            tool.execute(&json!({}), &config()).await.unwrap(),
            "No command provided."
        );
        assert_eq!(
            tool.execute(&json!({"command": "sudo ls"}), &config()).await.unwrap(),
            "Blocked command detected: sudo"
        );
    }

    #[cfg(unix)]
    #[tokio::test]
    async fn execute_captures_output_and_exit_code() {
        let tool = ShellTool;
        let cfg = config();
        assert_eq!(
            tool.execute(&json!({"command": "echo hello"}), &cfg).await.unwrap(),
            "hello"
        );
        assert_eq!(
            tool.execute(&json!({"command": "true"}), &cfg).await.unwrap(),
            "Command completed successfully (exit code: 0)"
        );
        let out = tool
            .execute(&json!({"command": "ls /definitely/not/here"}), &cfg)
            .await
            .unwrap();
        assert!(out.contains("stderr:"), "{out}");
        assert!(out.ends_with("Exit code: 2") || out.contains("Exit code:"), "{out}");
        assert_eq!(
            tool.execute(&json!({"command": "echo one; echo two"}), &cfg)
                .await
                .unwrap(),
            "one\ntwo"
        );
        assert_eq!(
            tool.execute(&json!({"command": "/nonexistent/dir/binary-xyz --flag"}), &cfg)
                .await
                .unwrap(),
            "Command not found: /nonexistent/dir/binary-xyz"
        );
    }

    #[cfg(unix)]
    #[tokio::test]
    async fn execute_times_out() {
        let cfg = HashConfig {
            command_timeout: 1,
            ..config()
        };
        let out = ShellTool.execute(&json!({"command": "sleep 5"}), &cfg).await.unwrap();
        assert_eq!(out, "Command timed out after 1 seconds");
    }

    #[cfg(unix)]
    #[tokio::test]
    async fn passthrough_skips_capture() {
        let cfg = config();
        let ok = ShellTool
            .execute(&json!({"command": "true", "passthrough_output": true}), &cfg)
            .await
            .unwrap();
        assert_eq!(ok, "");
        let failed = ShellTool
            .execute(&json!({"command": "false", "passthrough_output": true}), &cfg)
            .await
            .unwrap();
        assert_eq!(failed, "Exit code: 1");
    }
}
