use async_trait::async_trait;

use super::{Command, CommandProxy};
use crate::config::HashConfig;

pub struct HelpCommand;

impl HelpCommand {
    fn general_help(config: &HashConfig) -> String {
        let proxy = CommandProxy::new(config);
        let plugins: Vec<String> = proxy
            .available_commands()
            .into_iter()
            .filter(|name| proxy.is_plugin(name))
            .collect();

        let mut text = String::from(
            "Hash CLI - Command Mode

CORE COMMANDS:
  /help [command]             - Show help (this message)
  /history [options]          - Manage conversation history
  /config [get|set|unset]     - Manage configuration
  /tldr <command>             - Show quick examples for a command",
        );

        if plugins.is_empty() {
            text.push_str("\n\nPLUGINS:\n  None installed. Install with `hashcli --add-cmd <path-to-plugin>`.");
        } else {
            text.push_str("\n\nPLUGINS:");
            for name in plugins {
                text.push_str(&format!("\n  /{name}"));
            }
        }

        text.push_str(
            "

EXAMPLES:
  hi /help history
  hi /history list
  hi config get streaming

For command-specific help: /help <command>",
        );
        text
    }

    fn command_help(name: &str, config: &HashConfig) -> String {
        let proxy = CommandProxy::new(config);
        match proxy.command_help(name) {
            Some(help) if !help.is_empty() => format!("Help for /{name}:\n\n{help}"),
            _ => format!(
                "Unknown command: /{name}\n\nAvailable commands: {}\n\nUse '/help' for full help.",
                proxy.available_commands().join(", ")
            ),
        }
    }
}

#[async_trait(?Send)]
impl Command for HelpCommand {
    async fn execute(&self, args: &[String], config: &HashConfig) -> anyhow::Result<String> {
        Ok(match args.first().filter(|a| !a.is_empty()) {
            Some(name) => Self::command_help(name.trim_start_matches('/'), config),
            None => Self::general_help(config),
        })
    }

    fn help(&self) -> String {
        "Show help information:
  /help                    - Show general help and all commands
  /help <command>          - Show help for specific command

Examples:
  /help                    - Show this help
  /help history            - Show help for history command"
            .to_string()
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::commands::tests::test_config;
    use crate::config::tests::EnvGuard;

    #[tokio::test]
    async fn general_help_lists_core_commands() {
        let env = EnvGuard::new();
        let out = HelpCommand.execute(&[], &test_config(&env)).await.unwrap();
        assert!(out.contains("/history [options]"));
        assert!(out.contains("/tldr <command>"));
        assert!(out.contains("None installed."));
    }

    #[tokio::test]
    async fn specific_and_unknown_help() {
        let env = EnvGuard::new();
        let cfg = test_config(&env);
        let out = HelpCommand.execute(&["config".into()], &cfg).await.unwrap();
        assert!(out.starts_with("Help for /config:\n\nManage configuration:"));
        let unknown = HelpCommand.execute(&["nope".into()], &cfg).await.unwrap();
        assert!(unknown.starts_with("Unknown command: /nope"));
        assert!(unknown.contains("Available commands: config, help, history, tldr"));
    }

    #[test]
    fn help_help() {
        assert!(HelpCommand.help().contains("/help <command>"));
    }
}
