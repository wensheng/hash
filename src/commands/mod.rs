//! Slash-command routing (`/help`, `/history`, `/config`, `/tldr`, and installed plugins).

mod config;
mod help;
mod history;

use std::collections::BTreeMap;

use async_trait::async_trait;

use crate::config::HashConfig;
use crate::plugins;
use crate::shlex;
use crate::tldr::TldrCommand;

pub use config::ConfigCommand;
pub use help::HelpCommand;
pub use history::HistoryCommand;

/// Names that plugins may not take over.
pub const BUILTIN_COMMANDS: &[&str] = &["help", "history", "config", "tldr"];

#[async_trait(?Send)]
pub trait Command {
    async fn execute(&self, args: &[String], config: &HashConfig) -> anyhow::Result<String>;

    fn help(&self) -> String;

    fn validate_args(&self, _args: &[String]) -> bool {
        true
    }
}

pub struct CommandProxy {
    config: HashConfig,
    commands: BTreeMap<String, Box<dyn Command>>,
}

impl CommandProxy {
    pub fn new(config: &HashConfig) -> Self {
        let mut commands: BTreeMap<String, Box<dyn Command>> = BTreeMap::new();
        commands.insert("help".into(), Box::new(HelpCommand));
        commands.insert("history".into(), Box::new(HistoryCommand));
        commands.insert("config".into(), Box::new(ConfigCommand));
        commands.insert("tldr".into(), Box::new(TldrCommand));

        for plugin in plugins::discover_plugins() {
            if commands.contains_key(&plugin.name) {
                if config.show_debug {
                    eprintln!(
                        "{}",
                        crate::ui::yellow(&format!(
                            "Skipping plugin '{}': /{} already exists.",
                            plugin.path.display(),
                            plugin.name
                        ))
                    );
                }
                continue;
            }
            commands.insert(plugin.name.clone(), Box::new(plugins::PluginCommand::new(plugin)));
        }

        CommandProxy {
            config: config.clone(),
            commands,
        }
    }

    /// Execute a slash command line such as `/history show abc`.
    pub async fn execute(&self, command_line: &str) -> String {
        let command_line = command_line.trim_start().trim_start_matches('/');
        if command_line.is_empty() {
            return "No command specified. Use /help for available commands.".to_string();
        }
        let parts = match shlex::split(command_line) {
            Ok(parts) => parts,
            Err(e) => return format!("Error parsing command: {e}"),
        };
        let Some((cmd, args)) = parts.split_first() else {
            return "No command specified. Use /help for available commands.".to_string();
        };
        let Some(handler) = self.commands.get(cmd) else {
            return format!("Unknown command: /{cmd}\nUse /help for available commands.");
        };
        if !handler.validate_args(args) {
            return format!("Invalid arguments for /{cmd}\n{}", handler.help());
        }
        match handler.execute(args, &self.config).await {
            Ok(output) => output,
            Err(e) if self.config.show_debug => format!("Command execution error: {e:#}\n{e:?}"),
            Err(e) => format!("Command execution error: {e:#}"),
        }
    }

    pub fn is_plugin(&self, name: &str) -> bool {
        self.commands.contains_key(name) && !BUILTIN_COMMANDS.contains(&name)
    }

    pub fn available_commands(&self) -> Vec<String> {
        self.commands.keys().cloned().collect()
    }

    pub fn command_help(&self, name: &str) -> Option<String> {
        self.commands.get(name).map(|c| c.help())
    }
}

#[cfg(test)]
pub(crate) mod tests {
    use super::*;
    use crate::config::tests::EnvGuard;

    pub(crate) fn test_config(env: &EnvGuard) -> HashConfig {
        HashConfig {
            history_dir: env.home.path().join("history"),
            ..Default::default()
        }
    }

    #[tokio::test]
    async fn registers_builtins_only_without_plugins() {
        let env = EnvGuard::new();
        let proxy = CommandProxy::new(&test_config(&env));
        assert_eq!(proxy.available_commands(), vec!["config", "help", "history", "tldr"]);
        assert!(!proxy.is_plugin("help"));
    }

    #[tokio::test]
    async fn parses_and_routes_commands() {
        let env = EnvGuard::new();
        let proxy = CommandProxy::new(&test_config(&env));
        assert_eq!(
            proxy.execute("/").await,
            "No command specified. Use /help for available commands."
        );
        assert_eq!(
            proxy.execute("/nope arg").await,
            "Unknown command: /nope\nUse /help for available commands."
        );
        assert_eq!(
            proxy.execute("/help 'unterminated").await,
            "Error parsing command: No closing quotation"
        );
        assert!(proxy.execute("//help").await.starts_with("Hash CLI - Command Mode"));
        assert!(proxy.execute("/help history").await.starts_with("Help for /history:"));
    }

    #[tokio::test]
    async fn command_help_lookup() {
        let env = EnvGuard::new();
        let proxy = CommandProxy::new(&test_config(&env));
        assert!(proxy.command_help("history").unwrap().contains("/history list"));
        assert!(proxy.command_help("missing").is_none());
    }
}
