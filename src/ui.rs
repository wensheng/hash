//! Terminal output helpers: styling, panels, spinners, and prompts.
//!
//! Prompts fall back to reading plain lines from stdin when not attached to a terminal,
//! which keeps scripted setup (`printf '1\n...' | hashcli --config`) working.

use std::io::{self, BufRead, IsTerminal, Write};
use std::time::Duration;

use console::{Style, measure_text_width, style};

use crate::config::HashConfig;

pub fn is_interactive() -> bool {
    io::stdin().is_terminal()
}

pub fn stdout_is_terminal() -> bool {
    io::stdout().is_terminal()
}

pub fn bold(text: &str) -> String {
    style(text).bold().to_string()
}

pub fn dim(text: &str) -> String {
    style(text).dim().to_string()
}

pub fn cyan(text: &str) -> String {
    style(text).cyan().to_string()
}

pub fn green(text: &str) -> String {
    style(text).green().to_string()
}

pub fn yellow(text: &str) -> String {
    style(text).yellow().to_string()
}

pub fn red(text: &str) -> String {
    style(text).red().to_string()
}

pub fn bold_color(text: &str, color: console::Color) -> String {
    style(text).bold().fg(color).to_string()
}

fn terminal_width() -> usize {
    if !stdout_is_terminal() {
        return 80;
    }
    let (_, cols) = console::Term::stdout().size();
    if cols == 0 { 80 } else { cols as usize }
}

/// Fold a line into chunks no wider than `width` display columns.
fn fold_line(line: &str, width: usize) -> Vec<String> {
    if width == 0 || measure_text_width(line) <= width {
        return vec![line.to_string()];
    }
    let mut chunks = Vec::new();
    let mut current = String::new();
    let mut current_width = 0;
    let mut buf = [0u8; 4];
    for ch in line.chars() {
        let w = measure_text_width(ch.encode_utf8(&mut buf));
        if current_width + w > width && !current.is_empty() {
            chunks.push(std::mem::take(&mut current));
            current_width = 0;
        }
        current.push(ch);
        current_width += w;
    }
    if !current.is_empty() {
        chunks.push(current);
    }
    chunks
}

fn rule_with_title(left: &str, fill: &str, right: &str, title: &str, width: usize, color: console::Color) -> String {
    let border = Style::new().fg(color);
    let inner = width.saturating_sub(measure_text_width(left) + measure_text_width(right));
    let title_text = format!(" {title} ");
    let title_width = measure_text_width(&title_text);
    if title.is_empty() || title_width + 2 > inner {
        return border
            .apply_to(format!("{left}{}{right}", fill.repeat(inner)))
            .to_string();
    }
    let before = (inner - title_width) / 2;
    let after = inner - title_width - before;
    format!(
        "{}{}{}",
        border.apply_to(format!("{left}{}", fill.repeat(before))),
        style(title_text).bold().fg(color),
        border.apply_to(format!("{}{right}", fill.repeat(after)))
    )
}

/// Render a panel with horizontal rules above and below (Rich `box.HORIZONTALS`).
pub fn horizontal_panel(title: &str, body: &str, color: console::Color) -> String {
    let width = terminal_width();
    let border = Style::new().fg(color);
    let mut out = String::new();
    out.push_str(&rule_with_title("", "─", "", title, width, color));
    out.push('\n');
    for line in body.split('\n') {
        for chunk in fold_line(line, width.saturating_sub(2)) {
            out.push(' ');
            out.push_str(&chunk);
            out.push('\n');
        }
    }
    out.push_str(&border.apply_to("─".repeat(width)).to_string());
    out
}

/// Render a rounded box panel (Rich default `Panel`).
pub fn boxed_panel(title: &str, body: &str, color: console::Color) -> String {
    let width = terminal_width().min(100);
    let border = Style::new().fg(color);
    let inner = width.saturating_sub(4);
    let mut out = String::new();
    out.push_str(&rule_with_title("╭", "─", "╮", title, width, color));
    out.push('\n');
    for line in body.split('\n') {
        for chunk in fold_line(line, inner) {
            let pad = inner.saturating_sub(measure_text_width(&chunk));
            out.push_str(&format!(
                "{} {}{} {}\n",
                border.apply_to("│"),
                chunk,
                " ".repeat(pad),
                border.apply_to("│")
            ));
        }
    }
    out.push_str(
        &border
            .apply_to(format!("╰{}╯", "─".repeat(width.saturating_sub(2))))
            .to_string(),
    );
    out
}

/// Display a command or LLM result with formatting appropriate to the config.
pub fn display_result(result: &str, config: &HashConfig, quiet: bool) {
    if result.is_empty() {
        return;
    }
    if quiet {
        println!("{result}");
    } else if config.rich_output {
        println!();
        println!("{}", horizontal_panel("Result", result, console::Color::Green));
    } else {
        println!("\n{}", bold_color("Result:", console::Color::Green));
        println!("{result}");
    }
}

/// Print an error the way the CLI reports failures.
pub fn handle_error(error: &anyhow::Error, debug: bool) {
    if debug {
        eprintln!("\n{}", style("Debug Error Details:").bold().red().for_stderr());
        eprintln!("{error:?}");
    } else {
        eprintln!("\n{} {error:#}", style("Error:").bold().red().for_stderr());
        eprintln!("{}", style("Use --debug for more details").dim().for_stderr());
    }
}

/// Spinner on stderr that clears itself when dropped. No-op when stderr is not a terminal.
pub struct Spinner(Option<indicatif::ProgressBar>);

impl Spinner {
    pub fn new(message: &str) -> Self {
        if !io::stderr().is_terminal() {
            return Spinner(None);
        }
        let bar = indicatif::ProgressBar::new_spinner();
        bar.set_style(
            indicatif::ProgressStyle::with_template("{spinner:.green} {msg}")
                .unwrap_or_else(|_| indicatif::ProgressStyle::default_spinner()),
        );
        bar.set_message(style(message).dim().to_string());
        bar.enable_steady_tick(Duration::from_millis(100));
        Spinner(Some(bar))
    }
}

impl Drop for Spinner {
    fn drop(&mut self) {
        if let Some(bar) = self.0.take() {
            bar.finish_and_clear();
        }
    }
}

fn read_stdin_line(prompt: &str) -> Option<String> {
    eprint!("{prompt}");
    let _ = io::stderr().flush();
    let mut line = String::new();
    match io::stdin().lock().read_line(&mut line) {
        Ok(0) | Err(_) => None,
        Ok(_) => Some(line.trim_end_matches(['\r', '\n']).to_string()),
    }
}

/// Ask a yes/no question. Returns `default` on EOF or errors.
pub fn confirm(question: &str, default: bool) -> bool {
    if is_interactive() {
        return dialoguer::Confirm::new()
            .with_prompt(question)
            .default(default)
            .interact()
            .unwrap_or(default);
    }
    let hint = if default { "[Y/n]" } else { "[y/N]" };
    loop {
        let Some(answer) = read_stdin_line(&format!("{question} {hint}: ")) else {
            return default;
        };
        match answer.trim().to_lowercase().as_str() {
            "" => return default,
            "y" | "yes" => return true,
            "n" | "no" => return false,
            _ => eprintln!("Please enter Y or N"),
        }
    }
}

/// Ask for a line of text. Returns `default` (or an error if none) on EOF.
pub fn prompt_text(question: &str, default: Option<&str>, hide_input: bool) -> anyhow::Result<String> {
    if is_interactive() {
        let answer = if hide_input {
            dialoguer::Password::new()
                .with_prompt(question)
                .allow_empty_password(true)
                .interact()?
        } else {
            let mut input = dialoguer::Input::<String>::new().with_prompt(question);
            if let Some(d) = default {
                input = input.default(d.to_string()).allow_empty(d.is_empty());
            }
            input.interact_text()?
        };
        return Ok(if answer.is_empty() {
            default.unwrap_or("").to_string()
        } else {
            answer
        });
    }
    let label = match default {
        Some(d) if !d.is_empty() && !hide_input => format!("{question} ({d}): "),
        _ => format!("{question}: "),
    };
    match read_stdin_line(&label) {
        Some(answer) if !answer.trim().is_empty() => Ok(answer.trim().to_string()),
        Some(_) => match default {
            Some(d) => Ok(d.to_string()),
            None => anyhow::bail!("A value is required for: {question}"),
        },
        None => match default {
            Some(d) => Ok(d.to_string()),
            None => anyhow::bail!("Aborted: no input for: {question}"),
        },
    }
}

/// Ask until the answer is one of `choices`. Returns `default` on EOF.
pub fn prompt_choice(question: &str, choices: &[&str], default: &str) -> String {
    let label = format!("{question} [{}] ({default})", choices.join("/"));
    loop {
        let answer = if is_interactive() {
            dialoguer::Input::<String>::new()
                .with_prompt(&label)
                .allow_empty(true)
                .interact_text()
                .ok()
        } else {
            read_stdin_line(&format!("{label}: "))
        };
        let Some(answer) = answer else {
            return default.to_string();
        };
        let answer = answer.trim().to_lowercase();
        if answer.is_empty() {
            return default.to_string();
        }
        if choices.contains(&answer.as_str()) {
            return answer;
        }
        eprintln!("Please select one of the available options");
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn fold_line_splits_long_lines() {
        assert_eq!(fold_line("abcdef", 4), vec!["abcd", "ef"]);
        assert_eq!(fold_line("abc", 4), vec!["abc"]);
        assert_eq!(fold_line("", 4), vec![""]);
    }

    #[test]
    fn horizontal_panel_contains_title_and_body() {
        let rendered =
            console::strip_ansi_codes(&horizontal_panel("Result", "hello\nworld", console::Color::Green)).to_string();
        let lines: Vec<&str> = rendered.lines().collect();
        assert!(lines[0].contains(" Result "));
        assert_eq!(lines[1], " hello");
        assert_eq!(lines[2], " world");
        assert!(lines[3].chars().all(|c| c == '─'));
    }

    #[test]
    fn boxed_panel_has_borders() {
        let rendered = console::strip_ansi_codes(&boxed_panel("Hash CLI", "hi", console::Color::Blue)).to_string();
        let lines: Vec<&str> = rendered.lines().collect();
        assert!(lines[0].starts_with('╭') && lines[0].contains("Hash CLI"));
        assert!(lines[1].starts_with("│ hi"));
        assert!(lines[2].starts_with('╰'));
    }
}
