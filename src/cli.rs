//! Command-line entry point: flag handling and routing between command and LLM modes.

use std::ffi::OsString;

use clap::Parser;
use serde_json::json;

use crate::commands::CommandProxy;
use crate::config::{self, ConfigError, HashConfig, LlmProvider};
use crate::llm::LlmHandler;
use crate::query::{self, QueryExecutionPolicy};
use crate::tools::{ShellTool, Tool};
use crate::{plugins, session, setup, ui};

pub const VERSION: &str = env!("CARGO_PKG_VERSION");

#[derive(Parser, Debug)]
#[command(
    name = "hashcli",
    about = "Hash - Intelligent CLI assistant with dual-mode functionality",
    disable_version_flag = true
)]
pub struct Cli {
    /// Query or command to execute. Use /command for proxy mode.
    pub query: Vec<String>,

    /// Enable debug output and detailed error information
    #[arg(short = 'd', long)]
    pub debug: bool,

    /// Path to custom configuration file
    #[arg(short = 'f', long = "config-file", value_name = "CONFIG_FILE")]
    pub config_file: Option<String>,

    /// Override default LLM model (e.g., gpt-5, claude-3-sonnet)
    #[arg(short = 'm', long)]
    pub model: Option<String>,

    /// Override LLM provider (openai/anthropic/google or o/a/g)
    #[arg(short = 'p', long)]
    pub provider: Option<String>,

    /// Start a new conversation session for this invocation.
    #[arg(short = 'n', long = "new-session")]
    pub new_session: bool,

    /// Resume a conversation session by full ID or unique prefix.
    #[arg(long)]
    pub session: Option<String>,

    /// Minimize output, show only results
    #[arg(short = 'q', long)]
    pub quiet: bool,

    /// Show version information and exit.
    #[arg(short = 'v', long)]
    pub version: bool,

    /// Show current configuration and exit.
    #[arg(short = 's', long = "show-config")]
    pub show_config: bool,

    /// Configure Hash CLI and install shell integration if needed.
    #[arg(short = 'c', long = "config")]
    pub configure: bool,

    /// Install a slash-command plugin from an executable file or folder.
    #[arg(short = 'a', long = "add-cmd", value_name = "PATH")]
    pub add_cmd: Option<String>,

    /// List installed slash-command plugins.
    #[arg(long = "list-plugins")]
    pub list_plugins: bool,

    /// Remove an installed slash-command plugin by command name.
    #[arg(long = "remove-cmd", value_name = "NAME")]
    pub remove_cmd: Option<String>,

    /// Confirm non-interactive plugin install/removal prompts.
    #[arg(short = 'y', long)]
    pub yes: bool,

    /// Print slash command names for shell completions.
    #[arg(long = "completion-commands", hide = true)]
    pub completion_commands: bool,
}

/// Options that take a separate value argument.
const VALUE_OPTIONS: &[&str] = &[
    "-f",
    "--config-file",
    "-m",
    "--model",
    "-p",
    "--provider",
    "--session",
    "-a",
    "--add-cmd",
    "--remove-cmd",
];

/// Insert `--` before a slash command so its own flags (e.g. `/tldr --search x`)
/// are passed through instead of being parsed as hashcli options.
pub fn protect_slash_command_args(args: Vec<OsString>) -> Vec<OsString> {
    let mut iter = args.into_iter();
    let mut out: Vec<OsString> = iter.next().into_iter().collect();
    let mut expect_value = false;
    while let Some(arg) = iter.next() {
        let text = arg.to_string_lossy();
        if expect_value {
            expect_value = false;
        } else if text == "--" {
            out.push(arg);
            out.extend(iter);
            return out;
        } else if text.starts_with('-') && text.len() > 1 {
            expect_value = VALUE_OPTIONS.contains(&text.as_ref());
        } else {
            if text.starts_with('/') {
                out.push(OsString::from("--"));
            }
            out.push(arg);
            out.extend(iter);
            return out;
        }
        out.push(arg);
    }
    out
}

/// Parse arguments, run, and return the process exit code.
pub fn run() -> i32 {
    let args = protect_slash_command_args(std::env::args_os().collect());
    let cli = match Cli::try_parse_from(args) {
        Ok(cli) => cli,
        Err(e) => {
            let _ = e.print();
            return e.exit_code();
        }
    };
    if cli.debug && std::env::var_os("RUST_LIB_BACKTRACE").is_none() {
        // SAFETY: single-threaded at this point; the runtime has not started.
        unsafe { std::env::set_var("RUST_LIB_BACKTRACE", "1") };
    }
    let runtime = match tokio::runtime::Builder::new_current_thread().enable_all().build() {
        Ok(rt) => rt,
        Err(e) => {
            eprintln!("Error: failed to start runtime: {e}");
            return 1;
        }
    };
    runtime.block_on(run_cli(cli))
}

pub async fn run_cli(cli: Cli) -> i32 {
    if cli.version {
        println!("Hash CLI version {VERSION}");
        return 0;
    }
    if cli.show_config {
        return show_config(cli.config_file.as_deref());
    }
    if cli.configure {
        return match setup::run_setup_wizard().await {
            Ok(true) => 0,
            Ok(false) => 1,
            Err(e) => {
                ui::handle_error(&e, cli.debug);
                1
            }
        };
    }
    if cli.completion_commands {
        print_completion_commands(cli.config_file.as_deref());
        return 0;
    }
    if cli.list_plugins {
        print_installed_plugins();
        return 0;
    }
    if let Some(name) = &cli.remove_cmd {
        return match plugins::remove_plugin(name, cli.yes) {
            Ok(path) => {
                println!("{} {}", ui::green("Removed plugin:"), path.display());
                0
            }
            Err(e) => {
                println!("{}", ui::red(&format!("{e:#}")));
                1
            }
        };
    }
    if let Some(source) = &cli.add_cmd {
        return match plugins::install_plugin(source, cli.yes) {
            Ok(installed) => {
                println!("{} {}", ui::green("Installed plugin:"), installed.path.display());
                println!("{} /{}", ui::green("Available slash command:"), installed.name);
                0
            }
            Err(e) => {
                println!("{}", ui::red(&format!("{e:#}")));
                1
            }
        };
    }

    if cli.query.is_empty() {
        show_welcome();
        return 0;
    }
    let raw_input = cli.query.join(" ");
    let input_text = query::normalize_shell_input(&raw_input);
    if input_text.trim().is_empty() {
        show_welcome();
        return 0;
    }

    match run_query(&cli, &raw_input, input_text).await {
        Ok(code) => code,
        Err(e) => {
            ui::handle_error(&e, cli.debug);
            if e.downcast_ref::<ConfigError>().is_some() {
                println!("\n{} Run `hashcli --config` to get started.", ui::bold("Tip:"));
            }
            1
        }
    }
}

/// Accept full provider names plus single-letter aliases.
pub fn resolve_provider_option(provider: &str) -> anyhow::Result<LlmProvider> {
    match provider.trim().to_lowercase().as_str() {
        "a" | "anthropic" => Ok(LlmProvider::Anthropic),
        "g" | "google" => Ok(LlmProvider::Google),
        "o" | "openai" => Ok(LlmProvider::Openai),
        _ => anyhow::bail!("Invalid provider: {provider}. Expected one of a/anthropic, g/google, o/openai."),
    }
}

async fn run_query(cli: &Cli, raw_input: &str, mut input_text: String) -> anyhow::Result<i32> {
    if cli.new_session && cli.session.is_some() {
        return Err(ConfigError::new("--session cannot be combined with --new-session.").into());
    }
    if input_text.starts_with("config ") {
        input_text = format!("/{input_text}");
    }

    if !input_text.starts_with('/') && setup::should_run_first_setup(cli.config_file.as_deref()) {
        println!(
            "{}",
            ui::yellow("No Hash CLI configuration or provider API key was found.")
        );
        if !setup::run_setup_wizard().await? {
            return Ok(1);
        }
    }

    let mut config = config::load_configuration(cli.config_file.as_deref(), cli.debug, cli.model.as_deref())?;
    if let Some(provider) = &cli.provider {
        config.llm_provider = resolve_provider_option(provider)?;
    }
    let policy = query::build_query_execution_policy(raw_input, &input_text, config.command_confirmation);

    if input_text.starts_with('/') {
        execute_command_mode(&input_text, &config, cli.quiet).await;
    } else {
        config::validate_api_setup(&config)?;
        let session_id = session::resolve_conversation_session_id(cli.new_session, cli.session.as_deref(), &config)?;
        execute_llm_mode(&input_text, &config, cli.quiet, session_id, policy).await?;
    }
    Ok(0)
}

pub async fn execute_command_mode(input_text: &str, config: &HashConfig, quiet: bool) {
    let proxy = CommandProxy::new(config);
    let name = input_text
        .trim_start()
        .trim_start_matches('/')
        .split_whitespace()
        .next()
        .unwrap_or("");
    // Plugins may prompt on the terminal, so only show a spinner for built-ins.
    let spinner = (!quiet && !proxy.is_plugin(name)).then(|| ui::Spinner::new("Executing command..."));
    let result = proxy.execute(input_text).await;
    drop(spinner);
    ui::display_result(&result, config, quiet);
}

/// Prints complete streamed lines, hiding model-emitted execution prompts.
#[derive(Default)]
struct StreamPrinter {
    emitted: bool,
    buffer: String,
    line_buffer: String,
}

impl StreamPrinter {
    fn push(&mut self, chunk: &str) {
        if chunk.is_empty() {
            return;
        }
        self.emitted = true;
        self.buffer.push_str(chunk);
        let text = std::mem::take(&mut self.line_buffer) + chunk;
        let mut lines: Vec<&str> = text.split('\n').collect();
        self.line_buffer = lines.pop().unwrap_or("").to_string();
        for line in lines {
            if !query::is_execute_prompt_line(line) {
                println!("{line}");
            }
        }
    }

    fn finish(&mut self) {
        let tail = std::mem::take(&mut self.line_buffer);
        if !tail.is_empty() && !query::is_execute_prompt_line(&tail) {
            println!("{tail}");
        }
        println!();
    }
}

pub async fn execute_llm_mode(
    input_text: &str,
    config: &HashConfig,
    quiet: bool,
    session_id: Option<String>,
    policy: QueryExecutionPolicy,
) -> anyhow::Result<()> {
    let mut handler = LlmHandler::new(config, session_id)?;
    let should_suggest = policy.force_command_oriented || query::is_command_oriented_query(input_text);

    if config.streaming {
        let mut printer = StreamPrinter::default();
        let mut on_chunk = |chunk: &str| printer.push(chunk);
        let result = handler
            .chat(input_text, Some(&mut on_chunk), policy.force_tool_confirmation)
            .await;
        let mut final_text = result.clone();
        if printer.emitted {
            printer.finish();
            if final_text.is_empty() {
                final_text = printer.buffer.clone();
            }
        } else if !result.is_empty() {
            ui::display_result(&query::strip_execute_prompt_lines(&result), config, quiet);
        }
        if !final_text.is_empty() && !handler.last_tool_calls_executed && should_suggest {
            maybe_execute_suggested_command(&final_text, config, quiet, input_text, policy.command_confirmation).await;
        }
        return Ok(());
    }

    if !quiet {
        println!("{}", ui::dim(&format!("Thinking with {}...", config.current_model())));
    }
    let result = handler.chat(input_text, None, policy.force_tool_confirmation).await;
    if !result.is_empty() {
        ui::display_result(&query::strip_execute_prompt_lines(&result), config, quiet);
        if !handler.last_tool_calls_executed && should_suggest {
            maybe_execute_suggested_command(&result, config, quiet, input_text, policy.command_confirmation).await;
        }
    }
    Ok(())
}

async fn maybe_execute_suggested_command(
    response_text: &str,
    config: &HashConfig,
    quiet: bool,
    user_query: &str,
    command_confirmation: bool,
) {
    let Some(mut command) =
        query::extract_suggested_command(response_text, Some(user_query), config.allow_shell_operators)
    else {
        return;
    };
    if command_confirmation || ShellTool::is_potentially_destructive_command(&command) {
        match confirm_suggested_command(&command, response_text, config).await {
            Some(confirmed) => command = confirmed,
            None => return,
        }
    }
    let arguments = json!({
        "command": command,
        "description": "User-confirmed command execution",
        "passthrough_output": true,
    });
    match ShellTool.execute(&arguments, config).await {
        Ok(result) => ui::display_result(&result, config, quiet),
        Err(e) => ui::display_result(&format!("Error executing command: {e:#}"), config, quiet),
    }
}

/// Prompt for a suggested command with execute/cancel/explain/edit choices.
async fn confirm_suggested_command(command: &str, response_text: &str, config: &HashConfig) -> Option<String> {
    if !ui::is_interactive() {
        println!(
            "{}",
            ui::yellow("Suggested command requires confirmation; skipping in non-interactive mode.")
        );
        return None;
    }
    let mut current = command.to_string();
    loop {
        let description = query::extract_command_description(response_text, &current);
        println!("\n{}", ui::bold_color("Suggested command", console::Color::Yellow));
        if !description.is_empty() {
            println!("Description: {description}");
        }
        println!("Command: {}", ui::cyan(&current));
        let choice = ui::prompt_choice(
            "Choose [y] execute, [n] cancel, [x] explain, [e] edit",
            &["y", "n", "x", "e"],
            "n",
        );
        match choice.as_str() {
            "y" => return Some(current),
            "x" => {
                let explanation = explain_command_with_llm(&current, config).await;
                ui::display_result(&explanation, config, false);
            }
            "e" => current = edit_command_in_editor(&current),
            _ => return None,
        }
    }
}

async fn explain_command_with_llm(command: &str, config: &HashConfig) -> String {
    let explanation_config = HashConfig {
        allow_command_execution: false,
        tool_confirmation: true,
        command_confirmation: true,
        streaming: false,
        ..config.clone()
    };
    let mut handler = match LlmHandler::new(&explanation_config, None) {
        Ok(h) => h,
        Err(e) => return format!("LLM Error: {e:#}"),
    };
    let prompt = format!("Explain what this shell command does, piece by piece, without executing it:\n{command}");
    handler.chat(&prompt, None, Some(true)).await
}

/// Open the command in `$VISUAL`/`$EDITOR`; returns the edited (or original) command.
fn edit_command_in_editor(command: &str) -> String {
    let editor = std::env::var("VISUAL")
        .ok()
        .filter(|e| !e.trim().is_empty())
        .or_else(|| std::env::var("EDITOR").ok().filter(|e| !e.trim().is_empty()));
    let Some(editor) = editor else {
        println!("{}", ui::yellow("Set VISUAL or EDITOR to edit suggested commands."));
        return command.to_string();
    };
    let result = (|| -> anyhow::Result<String> {
        let file = tempfile::Builder::new()
            .prefix("hashcli-command-")
            .suffix(".sh")
            .tempfile()?;
        std::fs::write(file.path(), format!("{command}\n"))?;
        let words = crate::shlex::split(&editor)?;
        let (program, args) = words
            .split_first()
            .ok_or_else(|| anyhow::anyhow!("empty editor command"))?;
        std::process::Command::new(program)
            .args(args)
            .arg(file.path())
            .status()?;
        Ok(std::fs::read_to_string(file.path())?.trim().to_string())
    })();
    match result {
        Ok(edited) if !edited.is_empty() => edited,
        Ok(_) => command.to_string(),
        Err(e) => {
            println!("{}", ui::red(&format!("Failed to edit command: {e:#}")));
            command.to_string()
        }
    }
}

fn enabled(flag: bool) -> &'static str {
    if flag { "Enabled" } else { "Disabled" }
}

fn yes_no(flag: bool) -> &'static str {
    if flag { "Yes" } else { "No" }
}

fn show_config(config_file: Option<&str>) -> i32 {
    let config = match config::load_configuration(config_file, false, None) {
        Ok(config) => config,
        Err(e) => {
            println!(
                "{} {e}",
                ui::bold_color("Error loading configuration:", console::Color::Red)
            );
            return 1;
        }
    };
    println!("\n{}", ui::bold_color("Hash CLI Configuration", console::Color::Blue));
    println!("Provider: {}", ui::cyan(config.llm_provider.as_str()));
    println!("Model: {}", ui::cyan(config.current_model()));
    let key_status = if config.current_api_key().is_some_and(|k| !k.is_empty()) {
        "✓ Set"
    } else {
        "✗ Not set"
    };
    println!("API Key: {}", ui::green(key_status));
    println!(
        "Command execution: {}",
        ui::cyan(enabled(config.allow_command_execution))
    );
    println!(
        "Command confirmation: {}",
        ui::cyan(yes_no(config.command_confirmation))
    );
    println!("Tool confirmation: {}", ui::cyan(yes_no(config.tool_confirmation)));
    let operators = if config.allow_shell_operators {
        "Allowed"
    } else {
        "Blocked"
    };
    println!("Shell operators (|, ;): {}", ui::cyan(operators));
    println!("History: {}", ui::cyan(enabled(config.history_enabled)));
    if config.history_enabled {
        println!(
            "History location: {}",
            ui::dim(&config.history_dir.display().to_string())
        );
    }
    println!("Streaming: {}", ui::cyan(enabled(config.streaming)));
    0
}

fn print_completion_commands(config_file: Option<&str>) {
    let config = config::load_configuration(config_file, false, None).unwrap_or_default();
    let proxy = CommandProxy::new(&config);
    for name in proxy.available_commands() {
        let help = proxy.command_help(&name).unwrap_or_default();
        println!("{name}\t{}", help.lines().next().unwrap_or(""));
    }
}

fn print_installed_plugins() {
    let plugins = plugins::list_plugins();
    if plugins.is_empty() {
        println!("No plugins installed.");
        return;
    }
    for plugin in plugins {
        println!("/{}\t{}\t{}", plugin.name, plugin.path.display(), plugin.help);
    }
}

fn show_welcome() {
    let h1 = |t: &str| console::style(t).bold().underlined().to_string();
    let h2 = |t: &str| console::style(t).bold().to_string();
    let code = |t: &str| console::style(t).cyan().to_string();
    let body = [
        h1("Hash CLI - Command Assistant"),
        String::new(),
        h2("Usage Modes"),
        String::new(),
        format!("{} (Natural language queries):", h2("LLM Chat Mode")),
        code("  hashcli how do I list large files?"),
        code("  hashcli what command shows disk usage by directory?"),
        code("  hashcli explain xargs"),
        String::new(),
        format!("{} (Direct commands with `/` prefix):", h2("Command Proxy Mode")),
        code("  hashcli /help"),
        code("  hashcli /history list"),
        code("  hashcli /tldr tar"),
        code("  hashcli --add-cmd plugins/hello.py"),
        String::new(),
        h2("Quick Start"),
        format!(" 1. Set your API key: {}", code("export OPENAI_API_KEY=\"your-key\"")),
        format!(" 2. Try: {}", code("hashcli how do I check disk usage?")),
        format!(" 3. Or: {} for available commands", code("hashcli /help")),
        String::new(),
        format!("For more help: {}", code("hashcli --help")),
    ]
    .join("\n");
    println!("{}", ui::boxed_panel("Hash CLI", &body, console::Color::Blue));
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn provider_aliases() {
        assert_eq!(resolve_provider_option("a").unwrap(), LlmProvider::Anthropic);
        assert_eq!(resolve_provider_option(" G ").unwrap(), LlmProvider::Google);
        assert_eq!(resolve_provider_option("openai").unwrap(), LlmProvider::Openai);
        assert!(
            resolve_provider_option("x")
                .unwrap_err()
                .to_string()
                .starts_with("Invalid provider: x.")
        );
    }

    #[test]
    fn parses_flags() {
        let cli = Cli::try_parse_from(["hashcli", "-q", "-p", "a", "list", "files", "-n"]).unwrap();
        assert!(cli.quiet && cli.new_session);
        assert_eq!(cli.provider.as_deref(), Some("a"));
        assert_eq!(cli.query, vec!["list", "files"]);
        let cli = Cli::try_parse_from(["hashcli", "-v"]).unwrap();
        assert!(cli.version);
        let err = Cli::try_parse_from(["hashcli", "-h"]).unwrap_err();
        assert_eq!(err.kind(), clap::error::ErrorKind::DisplayHelp);
    }

    fn os(args: &[&str]) -> Vec<OsString> {
        args.iter().map(OsString::from).collect()
    }

    #[test]
    fn slash_command_args_pass_through() {
        assert_eq!(
            protect_slash_command_args(os(&["hashcli", "-q", "/tldr", "--search", "ta"])),
            os(&["hashcli", "-q", "--", "/tldr", "--search", "ta"])
        );
        assert_eq!(
            protect_slash_command_args(os(&["hashcli", "-f", "/etc/x.toml", "list", "-q"])),
            os(&["hashcli", "-f", "/etc/x.toml", "list", "-q"])
        );
        assert_eq!(
            protect_slash_command_args(os(&["hashcli", "--model=gpt-5", "/help"])),
            os(&["hashcli", "--model=gpt-5", "--", "/help"])
        );
        let cli =
            Cli::try_parse_from(protect_slash_command_args(os(&["hashcli", "/tldr", "-p", "osx", "ls"]))).unwrap();
        assert_eq!(cli.query, vec!["/tldr", "-p", "osx", "ls"]);
        assert_eq!(cli.provider, None);
    }

    #[test]
    fn stream_printer_buffers_lines() {
        let mut printer = StreamPrinter::default();
        printer.push("hello ");
        printer.push("world\nSUGGESTED_COMMAND: ls\npartial");
        assert!(printer.emitted);
        assert_eq!(printer.line_buffer, "partial");
        assert_eq!(printer.buffer, "hello world\nSUGGESTED_COMMAND: ls\npartial");
    }
}
