//! Query classification and suggested-command extraction for LLM mode.

use std::cmp::Ordering;
use std::path::{Component, Path, PathBuf};
use std::sync::LazyLock;

use regex::Regex;

use crate::shlex;

fn re(pattern: &str) -> Regex {
    Regex::new(pattern).expect("valid regex")
}

static LEADING_HASH: LazyLock<Regex> = LazyLock::new(|| re(r"(?s)^\s*#(.*)$"));
static HOW_TO: LazyLock<Regex> = LazyLock::new(|| re(r"^(?:how to|how do i|how can i|what command|which command)\b"));
static EXECUTE_PROMPT: LazyLock<Regex> =
    LazyLock::new(|| re(r"(?i)^\s*(?:do you want(?: me to)? execute|would you like(?: me)? to execute)\b"));
static LIST_PREFIX: LazyLock<Regex> = LazyLock::new(|| re(r"^(?:[-*•]\s+|\d+[.)]\s+)"));
static BULLET_PREFIX: LazyLock<Regex> = LazyLock::new(|| re(r"^(?:[-*•]\s+)"));
static NUMBER_PREFIX: LazyLock<Regex> = LazyLock::new(|| re(r"^\d+[.)]\s+"));
static INLINE_CODE: LazyLock<Regex> = LazyLock::new(|| re(r"`[^`]+`"));
static SHELL_WORDS: LazyLock<Regex> =
    LazyLock::new(|| re(r"\b(?:command|cli|shell|terminal|bash|zsh|powershell|cmd)\b"));
static SHELL_CHARS: LazyLock<Regex> = LazyLock::new(|| re(r"[|;&<>`$()]"));
static ACTION_WORDS: LazyLock<Regex> = LazyLock::new(|| {
    re(
        r"\b(?:how do i|how to|command|cli|shell|terminal|bash|zsh|powershell|cmd|run|execute|install|uninstall|kill|stop|restart|delete|remove|list|show|check|find|read)\b",
    )
});
static COMMAND_TOKENS: LazyLock<Regex> =
    LazyLock::new(|| re(r"\b(?:ls|pwd|grep|find|cat|sed|awk|git|docker|kubectl|python|pytest|npm|yarn|pnpm)\b"));
static POLITE_WORDS: LazyLock<Regex> = LazyLock::new(|| re(r"\b(?:confirm|execute|execution|please)\b"));
static QUERY_TOKEN: LazyLock<Regex> = LazyLock::new(|| re(r"[A-Za-z0-9_./-]+"));
static WHITESPACE_DASH: LazyLock<Regex> = LazyLock::new(|| re(r"\s-"));
static WHITESPACE: LazyLock<Regex> = LazyLock::new(|| re(r"\s"));
static FENCE: LazyLock<Regex> = LazyLock::new(|| re(r"(?s)```[a-zA-Z0-9_-]*\s*([^`]+?)\s*```"));
static EXPLICIT_PATTERNS: LazyLock<Vec<Regex>> = LazyLock::new(|| {
    vec![
        re(r"(?i)do you want(?: me to)? execute\s+`([^`]+)`"),
        re(r"(?i)would you like(?: me)? to execute\s+`([^`]+)`"),
        re(r"(?i)SUGGESTED_COMMAND:\s*`?([^`\n]+)`?"),
    ]
});

const GENERAL_STARTERS: &[&str] = &[
    "why ",
    "what is ",
    "what are ",
    "who ",
    "where ",
    "when ",
    "explain ",
    "tell me about ",
];

const NON_COMMAND_FIRST_WORDS: &[&str] = &["this", "that", "it", "you", "your", "confirm", "execute", "please"];

const COMMAND_ALLOWLIST: &[&str] = &[
    "ls",
    "pwd",
    "whoami",
    "date",
    "tail",
    "head",
    "cat",
    "kill",
    "killall",
    "pkill",
    "lsof",
    "fuser",
    "grep",
    "rg",
    "find",
    "xargs",
    "sort",
    "uniq",
    "wc",
    "du",
    "df",
    "ps",
    "top",
    "git",
    "pip",
    "python",
    "pytest",
    "npm",
    "yarn",
    "pnpm",
    "node",
    "cargo",
    "go",
    "make",
    "docker",
    "docker-compose",
    "kubectl",
    "curl",
    "wget",
    "sed",
    "awk",
    "chmod",
    "chown",
    "cp",
    "mv",
    "rm",
    "mkdir",
    "rmdir",
    "touch",
    "tree",
    "uname",
    "whereis",
    "which",
    "stat",
    "ln",
    "tar",
    "zip",
    "unzip",
    "ssh",
    "scp",
    "less",
    "more",
    "echo",
    "printf",
    "env",
    "seq",
    "cut",
    "tr",
    "diff",
    "cmp",
];

/// Normalize shell-integration input that uses `#` markers.
///
/// `# question` becomes `question`; `hint # intent` becomes an execution request that
/// uses `hint` as the command hint.
pub fn normalize_shell_input(input: &str) -> String {
    if input.is_empty() {
        return String::new();
    }
    if let Some(caps) = LEADING_HASH.captures(input) {
        return caps[1].trim_start().to_string();
    }
    let Some((hint, intent)) = split_embedded_hint(input) else {
        return input.to_string();
    };
    format!(
        "Task: {intent}. Use `{hint}` as command hint. Execute a single concrete shell command now and return the command result."
    )
}

fn split_embedded_hint(input: &str) -> Option<(&str, &str)> {
    let index = input.find('#').filter(|i| *i > 0)?;
    let hint = input[..index].trim();
    let intent = input[index + 1..].trim();
    (!hint.is_empty() && !intent.is_empty()).then_some((hint, intent))
}

pub fn is_how_to_query(input: &str) -> bool {
    HOW_TO.is_match(&input.trim().to_lowercase())
}

pub fn is_embedded_hash_hint(raw_input: &str) -> bool {
    !raw_input.is_empty() && !LEADING_HASH.is_match(raw_input) && split_embedded_hint(raw_input).is_some()
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct QueryExecutionPolicy {
    pub force_tool_confirmation: Option<bool>,
    pub command_confirmation: bool,
    pub force_command_oriented: bool,
}

pub fn build_query_execution_policy(
    raw_input: &str,
    normalized_input: &str,
    command_confirmation: bool,
) -> QueryExecutionPolicy {
    if is_embedded_hash_hint(raw_input) {
        return QueryExecutionPolicy {
            force_tool_confirmation: Some(false),
            command_confirmation: false,
            force_command_oriented: true,
        };
    }
    if is_how_to_query(normalized_input) {
        return QueryExecutionPolicy {
            force_tool_confirmation: Some(true),
            command_confirmation: true,
            force_command_oriented: true,
        };
    }
    QueryExecutionPolicy {
        force_tool_confirmation: None,
        command_confirmation,
        force_command_oriented: false,
    }
}

pub fn is_execute_prompt_line(line: &str) -> bool {
    line.starts_with("SUGGESTED_COMMAND:") || EXECUTE_PROMPT.is_match(line)
}

/// Remove model-emitted execution prompt lines from displayed output.
pub fn strip_execute_prompt_lines(text: &str) -> String {
    text.lines()
        .filter(|line| !is_execute_prompt_line(line))
        .collect::<Vec<_>>()
        .join("\n")
        .trim()
        .to_string()
}

/// A short description from the lines near a suggested command.
pub fn extract_command_description(text: &str, command: &str) -> String {
    let lines: Vec<&str> = text.lines().map(str::trim).collect();
    let mut search: Vec<usize> = Vec::new();
    for (index, line) in lines.iter().enumerate() {
        if line.contains(command) {
            search.extend(index.saturating_sub(3)..=index);
        }
    }
    if search.is_empty() {
        search = (0..lines.len().min(3)).collect();
    }
    for index in search {
        let line = lines[index];
        if line.is_empty() || is_execute_prompt_line(line) || line.starts_with("```") {
            continue;
        }
        let line = LIST_PREFIX.replace(line, "");
        let line = INLINE_CODE.replace_all(line.trim(), "");
        let line = line.trim_matches([' ', ':', '-']);
        if !line.is_empty() && line.split_whitespace().count() >= 3 {
            return line.chars().take(160).collect();
        }
    }
    String::new()
}

/// Only suggest command execution for shell-oriented requests.
pub fn is_command_oriented_query(user_query: &str) -> bool {
    let query = user_query.trim().to_lowercase();
    if query.is_empty() || query.starts_with('/') {
        return false;
    }
    if GENERAL_STARTERS.iter().any(|s| query.starts_with(s)) && !SHELL_WORDS.is_match(&query) {
        return false;
    }
    SHELL_CHARS.is_match(&query) || ACTION_WORDS.is_match(&query) || COMMAND_TOKENS.is_match(&query)
}

fn clean_command(command: &str) -> String {
    let cleaned = command.trim().trim_end_matches(['?', '.', '!', ' ']);
    let chars: Vec<char> = cleaned.chars().collect();
    if chars.len() >= 2 && chars[0] == chars[chars.len() - 1] && "\"'`".contains(chars[0]) {
        return chars[1..chars.len() - 1].iter().collect::<String>().trim().to_string();
    }
    cleaned.to_string()
}

fn strip_bullet_prefix(line: &str) -> String {
    let stripped = line.trim();
    let stripped = BULLET_PREFIX.replace(stripped, "");
    let stripped = NUMBER_PREFIX.replace(&stripped, "");
    stripped.trim().to_string()
}

fn is_probable_command(command: &str, allow_shell_operators: bool) -> bool {
    if command.is_empty()
        || command.contains('\n')
        || command.contains('\r')
        || command.len() > 256
        || command.contains('?')
        || command.trim().ends_with(':')
    {
        return false;
    }
    let mut forbidden = vec!["&&", "||", "`", "$(", ">", "<"];
    if !allow_shell_operators {
        forbidden.extend([";", "|"]);
    }
    if forbidden.iter().any(|op| command.contains(op)) {
        return false;
    }
    if POLITE_WORDS.is_match(&command.to_lowercase()) {
        return false;
    }
    let Ok(parts) = shlex::split(command) else {
        return false;
    };
    let Some(first_raw) = parts.first() else {
        return false;
    };
    let first = first_raw.to_lowercase();
    if NON_COMMAND_FIRST_WORDS.contains(&first.as_str()) {
        return false;
    }
    first_raw.contains('/') || first_raw.starts_with("~/") || COMMAND_ALLOWLIST.contains(&first.as_str())
}

fn extract_prefixed_command(line: &str, allow_shell_operators: bool) -> Option<String> {
    for prefix in ["command", "run", "use", "try"] {
        let Some(head) = line.get(..prefix.len()) else {
            continue;
        };
        if !head.eq_ignore_ascii_case(prefix) {
            continue;
        }
        // Only explicit prefixes such as "Run: ls -la", "Use - ls", or "Try `git status`".
        let remainder = line[prefix.len()..].trim_start();
        if !remainder.starts_with([':', '-', '`', '$']) {
            continue;
        }
        let remainder = if remainder.starts_with([':', '-']) {
            remainder[1..].trim_start()
        } else {
            remainder
        };
        let candidate = clean_command(remainder);
        if is_probable_command(&candidate, allow_shell_operators) {
            return Some(candidate);
        }
    }
    None
}

fn extract_query_tokens(text: Option<&str>) -> Vec<String> {
    QUERY_TOKEN
        .find_iter(text.unwrap_or(""))
        .map(|m| m.as_str())
        .filter(|t| t.contains('/') || t.contains('.') || t.contains("__"))
        .map(str::to_lowercase)
        .collect()
}

fn lexical_normalize(path: &Path) -> PathBuf {
    let mut out = PathBuf::new();
    for component in path.components() {
        match component {
            Component::ParentDir => {
                out.pop();
            }
            Component::CurDir => {}
            other => out.push(other),
        }
    }
    out
}

fn relative_to_cwd(part: &str) -> Option<String> {
    let path = Path::new(part);
    let cwd = std::env::current_dir().ok()?;
    let cwd = std::fs::canonicalize(&cwd).unwrap_or(cwd);
    let resolved = std::fs::canonicalize(path).unwrap_or_else(|_| lexical_normalize(path));
    let relative = resolved.strip_prefix(&cwd).ok()?;
    let text = relative.to_string_lossy().into_owned();
    Some(if text.is_empty() { ".".to_string() } else { text })
}

fn normalize_command(command: &str, query_tokens: &[String]) -> String {
    let Ok(parts) = shlex::split(command) else {
        return command.to_string();
    };
    if parts.is_empty() {
        return command.to_string();
    }
    let mut normalized = Vec::new();
    let mut changed = false;
    for part in parts {
        if part.starts_with("/path/to/")
            && let Some(token) = query_tokens.iter().find(|t| t.ends_with(".md") || t.ends_with(".txt"))
        {
            normalized.push(token.clone());
            changed = true;
            continue;
        }
        if Path::new(&part).is_absolute()
            && let Some(relative) = relative_to_cwd(&part)
        {
            normalized.push(relative);
            changed = true;
            continue;
        }
        normalized.push(part);
    }
    // Keep the original text (and its quoting) when nothing was rewritten.
    if !changed {
        return command.to_string();
    }
    normalized
        .iter()
        .map(|part| {
            if WHITESPACE.is_match(part) {
                shlex::quote(part)
            } else {
                part.clone()
            }
        })
        .collect::<Vec<_>>()
        .join(" ")
}

#[derive(Debug)]
struct Candidate {
    score: f64,
    position: usize,
    command: String,
}

fn by_score_desc(a: &Candidate, b: &Candidate) -> Ordering {
    b.score
        .partial_cmp(&a.score)
        .unwrap_or(Ordering::Equal)
        .then(b.position.cmp(&a.position))
}

/// Pick the most likely command the model suggested, if any.
pub fn extract_suggested_command(text: &str, user_query: Option<&str>, allow_shell_operators: bool) -> Option<String> {
    if text.is_empty() {
        return None;
    }
    let query_tokens = extract_query_tokens(user_query);

    let make_candidate = |raw: &str, source: &str, position: usize, normalize: bool| -> Option<Candidate> {
        let mut cleaned = clean_command(raw);
        if !is_probable_command(&cleaned, allow_shell_operators) {
            return None;
        }
        if normalize {
            cleaned = normalize_command(&cleaned, &query_tokens);
        }
        let mut score: f64 = match source {
            "explicit" => 100.0,
            "fence" => 90.0,
            "dollar" => 85.0,
            "prefixed" => 80.0,
            "inline" => 70.0,
            "bare" => 60.0,
            _ => 50.0,
        };
        let parts = shlex::split(&cleaned).unwrap_or_else(|_| cleaned.split_whitespace().map(str::to_string).collect());
        if parts.len() >= 2 {
            score += 10.0;
        }
        if parts.len() == 1 {
            score -= 20.0;
        }
        let lowered = cleaned.to_lowercase();
        if query_tokens.iter().any(|t| lowered.contains(t.as_str())) {
            score += 20.0;
        }
        if WHITESPACE_DASH.is_match(&cleaned) {
            score += 5.0;
        }
        score -= cleaned.chars().count().min(200) as f64 / 200.0;
        Some(Candidate {
            score,
            position,
            command: cleaned,
        })
    };

    let mut candidates = Vec::new();

    for pattern in EXPLICIT_PATTERNS.iter() {
        for caps in pattern.captures_iter(text) {
            let position = caps.get(0).map_or(0, |m| m.start());
            candidates.extend(make_candidate(&caps[1], "explicit", position, false));
        }
    }

    for (index, caps) in FENCE.captures_iter(text).enumerate() {
        for line in caps[1].lines() {
            let mut stripped = line.trim();
            if stripped.is_empty() {
                continue;
            }
            if stripped.starts_with('$') {
                stripped = stripped.trim_start_matches('$').trim();
            }
            candidates.extend(make_candidate(stripped, "fence", index, true));
        }
    }

    for (index, line) in text.lines().enumerate() {
        let stripped = strip_bullet_prefix(line);
        if stripped.is_empty() {
            continue;
        }
        if let Some(prefixed) = extract_prefixed_command(&stripped, allow_shell_operators) {
            candidates.extend(make_candidate(&prefixed, "prefixed", index, true));
            continue;
        }
        if stripped.starts_with('$') {
            candidates.extend(make_candidate(
                stripped.trim_start_matches('$').trim(),
                "dollar",
                index,
                true,
            ));
        }
    }

    if candidates.is_empty() {
        return None;
    }

    if !query_tokens.is_empty() {
        let mut matched: Vec<&Candidate> = candidates
            .iter()
            .filter(|c| {
                let lowered = c.command.to_lowercase();
                query_tokens.iter().all(|t| lowered.contains(t.as_str()))
            })
            .collect();
        if !matched.is_empty() {
            matched.sort_by(|a, b| by_score_desc(a, b));
            return Some(matched[0].command.clone());
        }
    }

    candidates.sort_by(by_score_desc);
    candidates.into_iter().next().map(|c| c.command)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn normalizes_shell_input() {
        assert_eq!(normalize_shell_input("# how do I list files"), "how do I list files");
        assert_eq!(normalize_shell_input("  #   /help"), "/help");
        assert_eq!(
            normalize_shell_input("find # python files changed today"),
            "Task: python files changed today. Use `find` as command hint. \
             Execute a single concrete shell command now and return the command result."
        );
        assert_eq!(normalize_shell_input("plain query"), "plain query");
        assert_eq!(normalize_shell_input("find #"), "find #");
        assert_eq!(normalize_shell_input(""), "");
    }

    #[test]
    fn execution_policy() {
        let how_to = build_query_execution_policy("# how do I list files", "how do I list files", false);
        assert_eq!(
            how_to,
            QueryExecutionPolicy {
                force_tool_confirmation: Some(true),
                command_confirmation: true,
                force_command_oriented: true
            }
        );
        let action = build_query_execution_policy("list files", "list files", true);
        assert_eq!(action.force_tool_confirmation, None);
        assert!(action.command_confirmation);
        assert!(!action.force_command_oriented);
        let hint = build_query_execution_policy("find # big files", "Task: big files...", true);
        assert_eq!(hint.force_tool_confirmation, Some(false));
        assert!(!hint.command_confirmation);
        assert!(hint.force_command_oriented);
    }

    #[test]
    fn classifies_command_oriented_queries() {
        assert!(!is_command_oriented_query("what is the capital of France"));
        assert!(!is_command_oriented_query("/help"));
        assert!(is_command_oriented_query("how to list large files"));
        assert!(is_command_oriented_query("what is the shell command for disk usage"));
        assert!(is_command_oriented_query("git branches"));
        assert!(is_command_oriented_query("a | b"));
        assert!(!is_command_oriented_query("tell me a joke"));
    }

    #[test]
    fn strips_execute_prompt_lines() {
        let text = "Use ls.\nDo you want me to execute `ls -la`?\nSUGGESTED_COMMAND: ls -la";
        assert_eq!(strip_execute_prompt_lines(text), "Use ls.");
        assert_eq!(
            strip_execute_prompt_lines("Hi\nWould you like me to execute `pwd`?"),
            "Hi"
        );
    }

    #[test]
    fn ignores_general_prose() {
        let text = "Paris is the capital of France. Run with your heart.";
        assert_eq!(extract_suggested_command(text, Some("capital of france"), true), None);
    }

    #[test]
    fn extracts_explicit_suggestions() {
        let text = "List files with details.\ndo you want execute `ls -la`?";
        assert_eq!(
            extract_suggested_command(text, Some("list files"), true).as_deref(),
            Some("ls -la")
        );
        let text = "Try this.\nSUGGESTED_COMMAND: du -sh *";
        assert_eq!(extract_suggested_command(text, None, true).as_deref(), Some("du -sh *"));
        let text = "do you want execute `ls`?\nOr better:\ndo you want execute `ls -la`?";
        assert_eq!(extract_suggested_command(text, None, true).as_deref(), Some("ls -la"));
    }

    #[test]
    fn extracts_fenced_dollar_and_prefixed() {
        let text = "Use this:\n```bash\n$ find . -name '*.py' -mtime -7\n```";
        assert_eq!(
            extract_suggested_command(text, None, true).as_deref(),
            Some("find . -name '*.py' -mtime -7")
        );
        assert_eq!(
            extract_suggested_command("Run: git status", None, true).as_deref(),
            Some("git status")
        );
        assert_eq!(
            extract_suggested_command("$ df -h", None, true).as_deref(),
            Some("df -h")
        );
        assert_eq!(extract_suggested_command("Running late", None, true), None);
        assert_eq!(extract_suggested_command("```\nls | wc -l\n```", None, false), None);
        assert_eq!(extract_suggested_command("```\nls && rm x\n```", None, true), None);
    }

    #[test]
    fn prefers_candidates_matching_query_paths() {
        let text = "```\ncat /path/to/file.txt\n```\n```\nhead -n 5 notes.md\n```";
        assert_eq!(
            extract_suggested_command(text, Some("show notes.md"), true).as_deref(),
            Some("head -n 5 notes.md")
        );
        let text = "```\ncat /path/to/file\n```";
        assert_eq!(
            extract_suggested_command(text, Some("print readme.md"), true).as_deref(),
            Some("cat readme.md")
        );
    }

    #[test]
    fn extracts_descriptions() {
        let text = "- Show disk usage per directory:\n```\ndu -sh *\n```";
        assert_eq!(
            extract_command_description(text, "du -sh *"),
            "Show disk usage per directory"
        );
        assert_eq!(extract_command_description("short\n`ls`", "ls"), "");
    }
}
