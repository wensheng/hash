use async_trait::async_trait;

use super::Command;
use crate::config::HashConfig;
use crate::history::ConversationHistory;

pub struct HistoryCommand;

/// Python-style `repr()` for short user-provided strings.
fn quoted(text: &str) -> String {
    if text.contains('\'') && !text.contains('"') {
        format!("\"{text}\"")
    } else {
        format!("'{}'", text.replace('\'', "\\'"))
    }
}

fn truncate_chars(text: &str, max: usize) -> String {
    if text.chars().count() > max {
        let truncated: String = text.chars().take(max).collect();
        format!("{truncated}...")
    } else {
        text.to_string()
    }
}

#[async_trait(?Send)]
impl Command for HistoryCommand {
    async fn execute(&self, args: &[String], config: &HashConfig) -> anyhow::Result<String> {
        if !config.history_enabled {
            return Ok("History is disabled in configuration.".to_string());
        }
        let history = ConversationHistory::new(&config.history_dir)?;
        let action = args.first().map(String::as_str);

        match action {
            None | Some("list") => {
                let sessions = history.list_sessions(10)?;
                if sessions.is_empty() {
                    return Ok("No conversation history found.".to_string());
                }
                let mut output = String::from("Recent conversations:\n");
                for session in sessions {
                    output.push_str(&format!(
                        "  {}: {} ({} messages)\n",
                        session.id, session.created, session.message_count
                    ));
                }
                Ok(output.trim().to_string())
            }
            Some("show") if args.len() > 1 => {
                let requested = &args[1];
                let Some(session_id) = history.resolve_session_id(requested)? else {
                    let matches = history.find_session_ids(requested, 10)?;
                    if matches.len() > 1 {
                        let list: Vec<String> = matches.iter().map(|m| format!("  {m}")).collect();
                        return Ok(format!(
                            "Ambiguous session ID prefix {}. Matches:\n{}",
                            quoted(requested),
                            list.join("\n")
                        ));
                    }
                    return Ok(format!("No messages found for session {requested}"));
                };
                let messages = history.get_session_messages(&session_id)?;
                if messages.is_empty() {
                    return Ok(format!("No messages found for session {requested}"));
                }
                let mut output = format!("Conversation {session_id}:\n\n");
                for message in messages {
                    output.push_str(&format!(
                        "[{}] {}\n\n",
                        message.role.to_uppercase(),
                        truncate_chars(&message.content, 200)
                    ));
                }
                Ok(output.trim().to_string())
            }
            Some("clear") => Ok(match history.clear_all_history() {
                Ok(()) => "All conversation history cleared.".to_string(),
                Err(_) => "Failed to clear history.".to_string(),
            }),
            Some("search") if args.len() > 1 => {
                let query = args[1..].join(" ").trim().to_string();
                if query.is_empty() {
                    return Ok(format!("Search query is required.\n\n{}", self.help()));
                }
                let results = history.search_messages(&query, 50)?;
                if results.is_empty() {
                    return Ok(format!("No history matches found for {}.", quoted(&query)));
                }
                let mut output = format!("History matches for {}:\n", quoted(&query));
                for result in results.iter().take(20) {
                    let prefix: String = result.session_id.chars().take(12).collect();
                    let snippet = result.content.split_whitespace().collect::<Vec<_>>().join(" ");
                    let snippet = if snippet.chars().count() > 120 {
                        format!("{}...", snippet.chars().take(117).collect::<String>())
                    } else {
                        snippet
                    };
                    output.push_str(&format!(
                        "  {prefix}  {:<9} {}  {snippet}\n",
                        result.role.to_uppercase(),
                        result.timestamp
                    ));
                }
                Ok(output.trim().to_string())
            }
            _ => Ok(self.help()),
        }
    }

    fn help(&self) -> String {
        "Manage conversation history:
  /history list        - List recent conversations
  /history show <id>   - Show specific conversation by full ID or unique prefix
  /history search <q>  - Search messages across history
  /history clear       - Clear all history"
            .to_string()
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::commands::tests::test_config;
    use crate::config::tests::EnvGuard;

    fn args(list: &[&str]) -> Vec<String> {
        list.iter().map(|s| s.to_string()).collect()
    }

    #[tokio::test]
    async fn list_show_search_clear() {
        let env = EnvGuard::new();
        let cfg = test_config(&env);
        assert_eq!(
            HistoryCommand.execute(&[], &cfg).await.unwrap(),
            "No conversation history found."
        );

        let history = ConversationHistory::new(&cfg.history_dir).unwrap();
        history.start_session(Some("abc-123")).unwrap();
        history.add_message("abc-123", "user", "how do I list files").unwrap();
        history.add_message("abc-123", "assistant", &"x".repeat(250)).unwrap();
        history.start_session(Some("abd-456")).unwrap();

        let list = HistoryCommand.execute(&args(&["list"]), &cfg).await.unwrap();
        assert!(list.starts_with("Recent conversations:"));
        assert!(list.contains("abc-123:") && list.contains("(2 messages)"));

        let show = HistoryCommand.execute(&args(&["show", "abc-"]), &cfg).await.unwrap();
        assert!(show.starts_with("Conversation abc-123:"));
        assert!(show.contains("[USER] how do I list files"));
        assert!(show.contains(&format!("[ASSISTANT] {}...", "x".repeat(200))));

        let ambiguous = HistoryCommand.execute(&args(&["show", "ab"]), &cfg).await.unwrap();
        assert!(ambiguous.starts_with("Ambiguous session ID prefix 'ab'. Matches:"));

        let missing = HistoryCommand.execute(&args(&["show", "zzz"]), &cfg).await.unwrap();
        assert_eq!(missing, "No messages found for session zzz");

        let search = HistoryCommand
            .execute(&args(&["search", "list", "files"]), &cfg)
            .await
            .unwrap();
        assert!(search.starts_with("History matches for 'list files':"));
        assert!(search.contains("abc-123  USER"));

        let none = HistoryCommand
            .execute(&args(&["search", "nothing"]), &cfg)
            .await
            .unwrap();
        assert_eq!(none, "No history matches found for 'nothing'.");

        assert_eq!(
            HistoryCommand.execute(&args(&["clear"]), &cfg).await.unwrap(),
            "All conversation history cleared."
        );
        assert!(
            HistoryCommand
                .execute(&args(&["bogus"]), &cfg)
                .await
                .unwrap()
                .starts_with("Manage")
        );
    }

    #[tokio::test]
    async fn disabled_history() {
        let env = EnvGuard::new();
        let cfg = HashConfig {
            history_enabled: false,
            ..test_config(&env)
        };
        assert_eq!(
            HistoryCommand.execute(&[], &cfg).await.unwrap(),
            "History is disabled in configuration."
        );
    }
}
