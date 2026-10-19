use std::collections::BTreeMap;

use async_trait::async_trait;

use super::Command;
use crate::config::{self as cfg, HashConfig};

pub struct ConfigCommand;

#[async_trait(?Send)]
impl Command for ConfigCommand {
    async fn execute(&self, args: &[String], config: &HashConfig) -> anyhow::Result<String> {
        let Some(action) = args.first() else {
            return Ok(self.help());
        };
        match (action.as_str(), args.len()) {
            ("get", 2) => {
                let key = &args[1];
                Ok(match config.display_value(key) {
                    Some(value) => format!("{key} = {value}"),
                    None => format!("Unknown config key: {key}"),
                })
            }
            ("set", n) if n >= 3 => {
                let key = &args[1];
                let raw_value = args[2..].join(" ");
                let value = match cfg::parse_config_value(key, &raw_value) {
                    Ok(value) => value,
                    Err(e) => return Ok(e.to_string()),
                };
                let updates = BTreeMap::from([(key.clone(), value)]);
                Ok(match cfg::update_config_values(&updates, None) {
                    Ok(()) => format!("Set {key}."),
                    Err(_) => format!("Failed to update config key: {key}"),
                })
            }
            ("unset", 2) => {
                let key = &args[1];
                if cfg::field_kind(key).is_none() {
                    return Ok(format!("Unknown config key: {key}"));
                }
                Ok(match cfg::remove_config_keys(&[key.as_str()], None) {
                    Ok(()) => format!("Unset {key}."),
                    Err(_) => format!("Failed to unset config key: {key}"),
                })
            }
            _ => Ok(self.help()),
        }
    }

    fn help(&self) -> String {
        "Manage configuration:
  /config get <key>          - Show an effective config value
  /config set <key> <value>  - Persist a config value
  /config unset <key>        - Remove a persisted config key"
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
    async fn get_set_unset_preserve_comments() {
        let env = EnvGuard::new();
        let cfg_obj = test_config(&env);
        let path = cfg::user_config_path();
        std::fs::create_dir_all(path.parent().unwrap()).unwrap();
        std::fs::write(&path, "# keep me\nopenai_model = \"gpt-5-nano\" # inline\n").unwrap();

        assert_eq!(
            ConfigCommand
                .execute(&args(&["get", "streaming"]), &cfg_obj)
                .await
                .unwrap(),
            "streaming = false"
        );
        assert_eq!(
            ConfigCommand.execute(&args(&["get", "bogus"]), &cfg_obj).await.unwrap(),
            "Unknown config key: bogus"
        );
        assert_eq!(
            ConfigCommand
                .execute(&args(&["set", "streaming", "on"]), &cfg_obj)
                .await
                .unwrap(),
            "Set streaming."
        );
        assert_eq!(
            ConfigCommand
                .execute(&args(&["set", "openai_model", "gpt-5-mini"]), &cfg_obj)
                .await
                .unwrap(),
            "Set openai_model."
        );
        let text = std::fs::read_to_string(&path).unwrap();
        assert!(text.contains("# keep me"));
        assert!(text.contains("openai_model = \"gpt-5-mini\" # inline"));
        assert!(text.contains("streaming = true"));

        assert!(
            ConfigCommand
                .execute(&args(&["set", "command_timeout", "soon"]), &cfg_obj)
                .await
                .unwrap()
                .contains("must be an integer")
        );
        assert_eq!(
            ConfigCommand
                .execute(&args(&["unset", "streaming"]), &cfg_obj)
                .await
                .unwrap(),
            "Unset streaming."
        );
        assert!(!std::fs::read_to_string(&path).unwrap().contains("streaming"));
        assert!(
            ConfigCommand
                .execute(&args(&["get"]), &cfg_obj)
                .await
                .unwrap()
                .starts_with("Manage")
        );
    }
}
