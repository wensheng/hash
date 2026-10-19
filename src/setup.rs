//! Interactive setup wizard and shell-integration installation.

use std::collections::BTreeMap;
use std::path::{Path, PathBuf};

use include_dir::{Dir, include_dir};
use toml::Value;

use crate::config::{self, HashConfig, LlmProvider, home_dir};
use crate::llm::ChatMessage;
use crate::providers;
use crate::ui;

/// Shell integration scripts, embedded so the binary is self-contained.
static SHELL_SCRIPTS: Dir<'_> = include_dir!("$CARGO_MANIFEST_DIR/shell");

const POWERSHELL_NAMES: &[&str] = &["pwsh", "powershell", "powershell.exe", "pwsh.exe"];
const SUPPORTED_SHELLS: &[&str] = &[
    "zsh",
    "bash",
    "fish",
    "pwsh",
    "powershell",
    "powershell.exe",
    "pwsh.exe",
];

pub fn provider_api_env_vars(provider: LlmProvider) -> &'static [&'static str] {
    match provider {
        LlmProvider::Openai => &["OPENAI_API_KEY", "HASHCLI_OPENAI_API_KEY"],
        LlmProvider::Anthropic => &["ANTHROPIC_API_KEY", "HASHCLI_ANTHROPIC_API_KEY"],
        LlmProvider::Google => &["GOOGLE_API_KEY", "GEMINI_API_KEY", "HASHCLI_GOOGLE_API_KEY"],
    }
}

fn existing_provider_key(provider: LlmProvider) -> Option<String> {
    provider_api_env_vars(provider)
        .iter()
        .filter_map(|name| std::env::var(name).ok())
        .map(|v| v.trim().to_string())
        .find(|v| !v.is_empty())
}

fn has_provider_api_key_env() -> bool {
    LlmProvider::ALL
        .iter()
        .flat_map(|p| provider_api_env_vars(*p).iter())
        .any(|name| std::env::var(name).is_ok_and(|v| !v.trim().is_empty()))
}

fn has_config_file(config_file: Option<&str>) -> bool {
    match config_file {
        Some(file) => config::expand_home(Path::new(file)).exists(),
        None => config::get_config_paths().iter().any(|p| p.exists()),
    }
}

/// First run: interactive, no config file, and no provider key in the environment.
pub fn should_run_first_setup(config_file: Option<&str>) -> bool {
    ui::is_interactive() && !has_config_file(config_file) && !has_provider_api_key_env()
}

/// Send a minimal request to check that the provider credentials work.
pub async fn validate_setup_provider(config: &HashConfig) -> (bool, String) {
    let provider = providers::create_provider(config);
    let messages = [ChatMessage::system("Reply with exactly: ok"), ChatMessage::user("ok")];
    let response = provider.generate_response(&messages, None, None).await;
    let content = response.content.trim().to_string();
    let lowered = content.to_lowercase();
    let error_markers = [
        "authentication failed",
        "invalid",
        "api key",
        "rate limit",
        "quota",
        "model",
        "error",
    ];
    if error_markers.iter().any(|m| lowered.contains(m)) {
        return (false, content);
    }
    (true, if content.is_empty() { "ok".to_string() } else { content })
}

fn invalid_choice() -> anyhow::Result<bool> {
    println!("{}", ui::red("Invalid choice"));
    Ok(false)
}

/// Run the setup wizard. Returns `Ok(false)` when setup failed (already reported).
pub async fn run_setup_wizard() -> anyhow::Result<bool> {
    println!("\n{}", ui::bold_color("Hash CLI Setup Wizard", console::Color::Blue));
    println!("This will help you configure Hash CLI for first use.\n");

    println!("{}", ui::bold("1. Choose your LLM provider:"));
    println!("   1) OpenAI");
    println!("   2) Anthropic");
    println!("   3) Google Gemini");

    let provider = loop {
        let answer = ui::prompt_text("Enter choice (1-3)", None, false)?;
        match answer.trim().parse::<i64>() {
            Ok(1) => break LlmProvider::Openai,
            Ok(2) => break LlmProvider::Anthropic,
            Ok(3) => break LlmProvider::Google,
            Ok(_) => return invalid_choice(),
            Err(_) => println!("Error: '{}' is not a valid integer.", answer.trim()),
        }
    };
    println!("\n{}", ui::bold(&format!("{} selected", provider.display_name())));

    println!(
        "\n{}",
        ui::bold(&format!("2. Select {} model:", provider.display_name()))
    );
    let options = config::get_model_options(provider);
    for (i, model) in options.iter().enumerate() {
        println!("   {}) {model}", i + 1);
    }
    println!("   {}) Custom model name", options.len() + 1);

    let answer = ui::prompt_text(&format!("Select model (1-{})", options.len() + 1), Some("1"), false)?;
    let mut selected_model = config::default_model(provider).to_string();
    let mut api_key: Option<String> = None;
    if !answer.is_empty() && answer.chars().all(|c| c.is_ascii_digit()) {
        let choice: usize = answer.parse().unwrap_or(0);
        if (1..=options.len()).contains(&choice) {
            selected_model = options[choice - 1].to_string();
        } else if choice == options.len() + 1 {
            selected_model = ui::prompt_text("Enter custom model name", None, false)?;
        } else {
            return invalid_choice();
        }
    } else {
        // Backward-compatible scripted setup that supplies the provider, then the key.
        api_key = Some(answer);
    }

    let api_key = match api_key {
        Some(key) => key,
        None => ui::prompt_text(
            "\nEnter your API key (leave blank to use an environment variable)",
            Some(""),
            true,
        )?,
    };
    let api_key = api_key.trim().to_string();
    let use_env_key = api_key.is_empty();
    let effective_key = if use_env_key {
        existing_provider_key(provider)
    } else {
        Some(api_key.clone())
    };

    let streaming = ui::confirm("Enable streaming responses?", false);

    let mut updates = BTreeMap::new();
    updates.insert("llm_provider".to_string(), Value::String(provider.as_str().into()));
    updates.insert(
        format!("{}_model", provider.as_str()),
        Value::String(selected_model.clone()),
    );
    updates.insert("streaming".to_string(), Value::Boolean(streaming));
    if !api_key.is_empty() {
        updates.insert(format!("{}_api_key", provider.as_str()), Value::String(api_key.clone()));
    }
    if config::update_config_values(&updates, None).is_err() {
        println!("{}", ui::red("Failed to save configuration"));
        return Ok(false);
    }

    if let Some(key) = effective_key {
        let mut validation_config = config::load_configuration(None, false, None)?;
        validation_config.set_api_key(provider, Some(key));
        validation_config.llm_provider = provider;
        validation_config.set_model(provider, selected_model.clone());
        validation_config.streaming = false;
        let (ok, message) = validate_setup_provider(&validation_config).await;
        if !ok {
            println!("{} {message}", ui::red("Provider validation failed:"));
            return Ok(false);
        }
        println!("{}", ui::green("Provider validation succeeded."));
    } else if use_env_key {
        println!(
            "{}",
            ui::yellow(&format!(
                "Skipped provider validation because no API key was found in {}.",
                provider_api_env_vars(provider).join(", ")
            ))
        );
    }

    let status = ensure_shell_integration();

    println!("\n{}", ui::bold_color("Setup complete!", console::Color::Green));
    if api_key.is_empty() {
        println!("API key will be read from your environment.");
    } else {
        println!("API key has been saved to ~/.hashcli/config.toml");
    }
    match status {
        ShellSetupStatus::Installed => println!("Shell integration has been installed."),
        ShellSetupStatus::Skipped => println!("Shell integration is already configured. Skipped."),
        ShellSetupStatus::Unsupported => {
            println!("Shell integration was skipped because the current shell is not supported.")
        }
        ShellSetupStatus::Failed => println!("Shell integration could not be installed automatically."),
    }
    println!("Try: {}", ui::cyan("hashcli hello world"));
    Ok(true)
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum ShellSetupStatus {
    Installed,
    Skipped,
    Unsupported,
    Failed,
}

struct ShellMetadata {
    rc_file: Option<PathBuf>,
    source_line: Option<&'static str>,
    required_files: Vec<PathBuf>,
}

fn shell_metadata(shell: &str) -> Option<ShellMetadata> {
    let home = home_dir();
    let config_dir = home.join(".config");
    Some(match shell {
        "zsh" => ShellMetadata {
            rc_file: Some(home.join(".zshrc")),
            source_line: Some("source ~/.config/zsh/hash/hash.zsh"),
            required_files: vec![
                config_dir.join("zsh/hash/hash.zsh"),
                config_dir.join("zsh/hash/completions/_hash"),
            ],
        },
        "bash" => ShellMetadata {
            rc_file: Some(home.join(".bashrc")),
            source_line: Some("source ~/.config/bash/hash/hash.bash"),
            required_files: vec![
                config_dir.join("bash/hash/hash.bash"),
                config_dir.join("bash/hash/hash_completion.bash"),
            ],
        },
        "fish" => ShellMetadata {
            rc_file: None,
            source_line: None,
            required_files: vec![
                config_dir.join("fish/conf.d/hash_integration.fish"),
                config_dir.join("fish/completions/completions.fish"),
            ],
        },
        s if POWERSHELL_NAMES.contains(&s) => ShellMetadata {
            rc_file: None,
            source_line: None,
            required_files: vec![
                home.join(".hashcli/powershell/hash.ps1"),
                home.join(".hashcli/powershell/completions.ps1"),
            ],
        },
        _ => return None,
    })
}

pub fn is_shell_integration_installed(shell: &str) -> bool {
    let Some(meta) = shell_metadata(shell) else {
        return false;
    };
    if !meta.required_files.iter().all(|p| p.exists()) {
        return false;
    }
    match (meta.rc_file, meta.source_line) {
        (Some(rc), Some(line)) => std::fs::read(&rc)
            .map(|bytes| String::from_utf8_lossy(&bytes).contains(line))
            .unwrap_or(false),
        _ => true,
    }
}

fn make_scripts_executable(dir: &Path) {
    let Ok(entries) = std::fs::read_dir(dir) else {
        return;
    };
    for entry in entries.flatten() {
        let path = entry.path();
        if path.is_dir() {
            make_scripts_executable(&path);
            continue;
        }
        #[cfg(unix)]
        if path.extension().is_some_and(|e| e == "sh" || e == "bash" || e == "ps1") {
            use std::os::unix::fs::PermissionsExt;
            let _ = std::fs::set_permissions(&path, std::fs::Permissions::from_mode(0o755));
        }
    }
}

/// Write the embedded shell scripts to `dir`.
pub fn extract_shell_scripts(dir: &Path) -> std::io::Result<()> {
    std::fs::create_dir_all(dir)?;
    SHELL_SCRIPTS.extract(dir)?;
    make_scripts_executable(dir);
    Ok(())
}

/// Install shell integration for `$SHELL` if needed.
pub fn ensure_shell_integration() -> ShellSetupStatus {
    let shell_env = std::env::var("SHELL").unwrap_or_default();
    let shell = Path::new(&shell_env)
        .file_name()
        .map(|n| n.to_string_lossy().into_owned())
        .unwrap_or_default();

    if !SUPPORTED_SHELLS.contains(&shell.as_str()) {
        println!(
            "{}",
            ui::yellow("Shell integration setup supports zsh, bash, fish, and PowerShell.")
        );
        println!(
            "Detected shell: {}",
            ui::dim(if shell.is_empty() { "unknown" } else { &shell })
        );
        println!("For other shells, use the scripts in the shell directory.");
        return ShellSetupStatus::Unsupported;
    }
    if is_shell_integration_installed(&shell) {
        println!(
            "{}",
            ui::dim(&format!("{shell} shell integration already configured, skipping."))
        );
        return ShellSetupStatus::Skipped;
    }

    let user_shell_dir = config::hashcli_home().join("shell");
    let install_name = if POWERSHELL_NAMES.contains(&shell.as_str()) {
        "powershell"
    } else {
        shell.as_str()
    };
    let install_script = user_shell_dir.join(install_name).join(if install_name == "powershell" {
        "install.ps1"
    } else {
        "install.sh"
    });

    println!("{}", ui::dim("Setting up shell integration scripts..."));
    if let Err(e) = extract_shell_scripts(&user_shell_dir) {
        println!(
            "{} {e}",
            ui::bold_color("Failed to copy shell scripts:", console::Color::Red)
        );
        return ShellSetupStatus::Failed;
    }
    println!(
        "{}",
        ui::dim(&format!("Shell scripts copied to {}", user_shell_dir.display()))
    );

    println!(
        "{}",
        ui::bold_color(
            &format!("Installing {shell} shell integration..."),
            console::Color::Blue
        )
    );
    let status = if install_name == "powershell" {
        let Some(binary) = which::which("pwsh").or_else(|_| which::which("powershell")).ok() else {
            println!(
                "{}",
                ui::bold_color("PowerShell executable not found.", console::Color::Red)
            );
            return ShellSetupStatus::Failed;
        };
        std::process::Command::new(binary)
            .args(["-NoProfile", "-ExecutionPolicy", "Bypass", "-File"])
            .arg(&install_script)
            .status()
    } else {
        std::process::Command::new("/bin/bash")
            .arg(&install_script)
            .arg("install")
            .status()
    };
    match status {
        Ok(s) if s.success() => ShellSetupStatus::Installed,
        _ => {
            println!(
                "{}",
                ui::bold_color("Shell integration setup failed.", console::Color::Red)
            );
            ShellSetupStatus::Failed
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::config::tests::EnvGuard;

    #[test]
    fn embedded_scripts_extract() {
        let dir = tempfile::tempdir().unwrap();
        extract_shell_scripts(dir.path()).unwrap();
        assert!(dir.path().join("zsh/hash.zsh").exists());
        assert!(dir.path().join("bash/install.sh").exists());
        assert!(dir.path().join("fish/hash.fish").exists());
        assert!(dir.path().join("powershell/install.ps1").exists());
        #[cfg(unix)]
        {
            use std::os::unix::fs::PermissionsExt;
            let mode = std::fs::metadata(dir.path().join("zsh/install.sh"))
                .unwrap()
                .permissions()
                .mode();
            assert_eq!(mode & 0o755, 0o755);
        }
    }

    #[test]
    fn detects_installed_integration() {
        let env = EnvGuard::new();
        assert!(!is_shell_integration_installed("zsh"));
        assert!(!is_shell_integration_installed("tcsh"));
        let home = env.home.path();
        std::fs::create_dir_all(home.join(".config/zsh/hash/completions")).unwrap();
        std::fs::write(home.join(".config/zsh/hash/hash.zsh"), "").unwrap();
        std::fs::write(home.join(".config/zsh/hash/completions/_hash"), "").unwrap();
        assert!(!is_shell_integration_installed("zsh"));
        std::fs::write(home.join(".zshrc"), "source ~/.config/zsh/hash/hash.zsh\n").unwrap();
        assert!(is_shell_integration_installed("zsh"));
    }

    #[test]
    fn unsupported_shell_is_reported() {
        let env = EnvGuard::new();
        env.set("SHELL", "/bin/tcsh");
        assert_eq!(ensure_shell_integration(), ShellSetupStatus::Unsupported);
    }

    #[test]
    fn first_setup_detection_respects_env_keys() {
        let env = EnvGuard::new();
        assert!(!has_provider_api_key_env());
        env.set("GEMINI_API_KEY", "x");
        assert!(has_provider_api_key_env());
        assert_eq!(existing_provider_key(LlmProvider::Google).as_deref(), Some("x"));
        assert!(!has_config_file(Some("/definitely/missing.toml")));
    }
}
