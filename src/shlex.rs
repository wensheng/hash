//! Shell-word splitting and quoting compatible with Python's `shlex.split` / `shlex.quote`.
//!
//! The behavior intentionally mirrors POSIX-mode `shlex` with comments disabled, so `#`
//! is an ordinary character (important for `#`-prefixed shell integration input).

use std::fmt;

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum SplitError {
    NoClosingQuotation,
    NoEscapedCharacter,
}

impl fmt::Display for SplitError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            SplitError::NoClosingQuotation => write!(f, "No closing quotation"),
            SplitError::NoEscapedCharacter => write!(f, "No escaped character"),
        }
    }
}

impl std::error::Error for SplitError {}

#[derive(Clone, Copy, PartialEq, Eq)]
enum State {
    Whitespace,
    Word,
    Quote(char),
    Escape,
}

fn is_whitespace(ch: char) -> bool {
    matches!(ch, ' ' | '\t' | '\r' | '\n')
}

/// Split a string into shell words like `shlex.split(s)`.
pub fn split(input: &str) -> Result<Vec<String>, SplitError> {
    let mut tokens = Vec::new();
    let mut token = String::new();
    let mut quoted = false;
    let mut state = State::Whitespace;
    let mut escaped_state = State::Word;

    for ch in input.chars() {
        match state {
            State::Whitespace => {
                if is_whitespace(ch) {
                    continue;
                } else if ch == '\\' {
                    escaped_state = State::Word;
                    state = State::Escape;
                } else if ch == '\'' || ch == '"' {
                    state = State::Quote(ch);
                } else {
                    token.push(ch);
                    state = State::Word;
                }
            }
            State::Word => {
                if is_whitespace(ch) {
                    tokens.push(std::mem::take(&mut token));
                    quoted = false;
                    state = State::Whitespace;
                } else if ch == '\'' || ch == '"' {
                    state = State::Quote(ch);
                } else if ch == '\\' {
                    escaped_state = State::Word;
                    state = State::Escape;
                } else {
                    token.push(ch);
                }
            }
            State::Quote(q) => {
                quoted = true;
                if ch == q {
                    state = State::Word;
                } else if ch == '\\' && q == '"' {
                    escaped_state = state;
                    state = State::Escape;
                } else {
                    token.push(ch);
                }
            }
            State::Escape => {
                if let State::Quote(q) = escaped_state
                    && ch != '\\'
                    && ch != q
                {
                    token.push('\\');
                }
                token.push(ch);
                state = escaped_state;
            }
        }
    }

    match state {
        State::Quote(_) => return Err(SplitError::NoClosingQuotation),
        State::Escape => return Err(SplitError::NoEscapedCharacter),
        State::Word => tokens.push(token),
        State::Whitespace => {
            if quoted {
                tokens.push(token);
            }
        }
    }
    Ok(tokens)
}

/// Quote a string for safe use as a single shell word, like `shlex.quote(s)`.
pub fn quote(s: &str) -> String {
    if s.is_empty() {
        return "''".to_string();
    }
    let safe = s.chars().all(|c| c.is_ascii_alphanumeric() || "_@%+=:,./-".contains(c));
    if safe {
        return s.to_string();
    }
    format!("'{}'", s.replace('\'', "'\"'\"'"))
}

#[cfg(test)]
mod tests {
    use super::*;

    fn s(v: &[&str]) -> Vec<String> {
        v.iter().map(|x| x.to_string()).collect()
    }

    #[test]
    fn splits_basic_words() {
        assert_eq!(split("ls -la /tmp").unwrap(), s(&["ls", "-la", "/tmp"]));
        assert_eq!(split("  a\t b\r\nc  ").unwrap(), s(&["a", "b", "c"]));
        assert_eq!(split("").unwrap(), Vec::<String>::new());
    }

    #[test]
    fn hash_is_not_a_comment() {
        assert_eq!(split("# hello world").unwrap(), s(&["#", "hello", "world"]));
        assert_eq!(split("echo a#b").unwrap(), s(&["echo", "a#b"]));
    }

    #[test]
    fn handles_quotes() {
        assert_eq!(split("echo 'a b' \"c d\"").unwrap(), s(&["echo", "a b", "c d"]));
        assert_eq!(split("a'b c'd").unwrap(), s(&["ab cd"]));
        assert_eq!(split("x '' y").unwrap(), s(&["x", "", "y"]));
        assert_eq!(split("\"\"").unwrap(), s(&[""]));
    }

    #[test]
    fn handles_escapes() {
        assert_eq!(split(r"a\ b").unwrap(), s(&["a b"]));
        assert_eq!(split(r#""a\"b""#).unwrap(), s(&["a\"b"]));
        assert_eq!(split(r#""a\nb""#).unwrap(), s(&[r"a\nb"]));
        assert_eq!(split(r#""a\\b""#).unwrap(), s(&[r"a\b"]));
        assert_eq!(split(r"'a\b'").unwrap(), s(&[r"a\b"]));
    }

    #[test]
    fn reports_errors() {
        assert_eq!(split("echo 'abc"), Err(SplitError::NoClosingQuotation));
        assert_eq!(split("echo abc\\"), Err(SplitError::NoEscapedCharacter));
        assert_eq!(SplitError::NoClosingQuotation.to_string(), "No closing quotation");
    }

    #[test]
    fn quotes_like_python() {
        assert_eq!(quote(""), "''");
        assert_eq!(quote("abc-1.txt"), "abc-1.txt");
        assert_eq!(quote("a b"), "'a b'");
        assert_eq!(quote("it's"), "'it'\"'\"'s'");
    }
}
