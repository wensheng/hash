//! Conversation history persisted in SQLite (schema-compatible with the Python version).

use std::path::{Path, PathBuf};

use anyhow::{Context, Result};
use rusqlite::{Connection, OptionalExtension, params};

#[derive(Debug, Clone, PartialEq)]
pub struct SessionInfo {
    pub id: String,
    pub created: String,
    pub updated: String,
    pub title: Option<String>,
    pub metadata: Option<serde_json::Value>,
    pub message_count: i64,
}

#[derive(Debug, Clone, PartialEq)]
pub struct StoredMessage {
    pub role: String,
    pub content: String,
    pub timestamp: String,
    pub metadata: Option<serde_json::Value>,
}

#[derive(Debug, Clone, PartialEq)]
pub struct SearchResult {
    pub session_id: String,
    pub session_title: Option<String>,
    pub role: String,
    pub content: String,
    pub timestamp: String,
}

pub struct ConversationHistory {
    conn: Connection,
    db_path: PathBuf,
}

const SCHEMA: &str = "
CREATE TABLE IF NOT EXISTS sessions (
    id TEXT PRIMARY KEY,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    title TEXT,
    metadata TEXT  -- JSON
);
CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL,
    role TEXT NOT NULL,
    content TEXT NOT NULL,
    timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    metadata TEXT,  -- JSON
    FOREIGN KEY (session_id) REFERENCES sessions (id)
);
CREATE INDEX IF NOT EXISTS idx_messages_session_id ON messages (session_id);
CREATE INDEX IF NOT EXISTS idx_messages_timestamp ON messages (timestamp);
";

fn parse_metadata(raw: Option<String>) -> Option<serde_json::Value> {
    raw.filter(|s| !s.is_empty())
        .and_then(|s| serde_json::from_str(&s).ok())
}

impl ConversationHistory {
    /// Open (and create if needed) the history database in `history_dir`.
    pub fn new(history_dir: &Path) -> Result<Self> {
        std::fs::create_dir_all(history_dir)
            .with_context(|| format!("creating history directory {}", history_dir.display()))?;
        let db_path = history_dir.join("conversations.db");
        let conn = Connection::open(&db_path).with_context(|| format!("opening {}", db_path.display()))?;
        conn.execute_batch(SCHEMA)?;
        Ok(ConversationHistory { conn, db_path })
    }

    pub fn db_path(&self) -> &Path {
        &self.db_path
    }

    /// Start a session, creating a random id when none is given. Existing ids are reused.
    pub fn start_session(&self, session_id: Option<&str>) -> Result<String> {
        let id = session_id
            .map(str::to_string)
            .unwrap_or_else(|| uuid::Uuid::new_v4().to_string());
        self.conn
            .execute("INSERT OR IGNORE INTO sessions (id) VALUES (?1)", params![id])?;
        Ok(id)
    }

    pub fn add_message(&self, session_id: &str, role: &str, content: &str) -> Result<()> {
        self.conn.execute(
            "INSERT INTO messages (session_id, role, content, metadata) VALUES (?1, ?2, ?3, NULL)",
            params![session_id, role, content],
        )?;
        self.conn.execute(
            "UPDATE sessions SET updated_at = CURRENT_TIMESTAMP WHERE id = ?1",
            params![session_id],
        )?;
        Ok(())
    }

    pub fn get_session_messages(&self, session_id: &str) -> Result<Vec<StoredMessage>> {
        let mut stmt = self.conn.prepare(
            "SELECT role, content, timestamp, metadata FROM messages
             WHERE session_id = ?1 ORDER BY timestamp ASC, id ASC",
        )?;
        let rows = stmt.query_map(params![session_id], |row| {
            Ok(StoredMessage {
                role: row.get(0)?,
                content: row.get(1)?,
                timestamp: row.get(2)?,
                metadata: parse_metadata(row.get(3)?),
            })
        })?;
        Ok(rows.collect::<rusqlite::Result<_>>()?)
    }

    /// Most recent `limit` messages of a session, in chronological order, as (role, content).
    pub fn get_recent_messages(&self, session_id: &str, limit: usize) -> Result<Vec<(String, String)>> {
        let mut stmt = self.conn.prepare(
            "SELECT role, content FROM messages
             WHERE session_id = ?1 ORDER BY timestamp DESC, id DESC LIMIT ?2",
        )?;
        let rows = stmt.query_map(params![session_id, limit as i64], |row| Ok((row.get(0)?, row.get(1)?)))?;
        let mut messages: Vec<(String, String)> = rows.collect::<rusqlite::Result<_>>()?;
        messages.reverse();
        Ok(messages)
    }

    /// Sessions ordered by most recently updated first.
    pub fn list_sessions(&self, limit: usize) -> Result<Vec<SessionInfo>> {
        let mut stmt = self.conn.prepare(
            "SELECT s.id, s.created_at, s.updated_at, s.title, s.metadata, COUNT(m.id)
             FROM sessions s LEFT JOIN messages m ON s.id = m.session_id
             GROUP BY s.id ORDER BY s.updated_at DESC, s.rowid DESC LIMIT ?1",
        )?;
        let rows = stmt.query_map(params![limit as i64], Self::session_from_row)?;
        Ok(rows.collect::<rusqlite::Result<_>>()?)
    }

    fn session_from_row(row: &rusqlite::Row<'_>) -> rusqlite::Result<SessionInfo> {
        Ok(SessionInfo {
            id: row.get(0)?,
            created: row.get::<_, Option<String>>(1)?.unwrap_or_default(),
            updated: row.get::<_, Option<String>>(2)?.unwrap_or_default(),
            title: row.get(3)?,
            metadata: parse_metadata(row.get(4)?),
            message_count: row.get(5)?,
        })
    }

    /// Session ids matching a prefix, most recently updated first.
    pub fn find_session_ids(&self, prefix: &str, limit: usize) -> Result<Vec<String>> {
        let prefix = prefix.trim();
        if prefix.is_empty() {
            return Ok(Vec::new());
        }
        let escaped = prefix.replace('\\', "\\\\").replace('%', "\\%").replace('_', "\\_");
        let mut stmt = self.conn.prepare(
            "SELECT id FROM sessions WHERE id LIKE ?1 ESCAPE '\\'
             ORDER BY updated_at DESC LIMIT ?2",
        )?;
        let rows = stmt.query_map(params![format!("{escaped}%"), limit as i64], |row| row.get(0))?;
        Ok(rows.collect::<rusqlite::Result<_>>()?)
    }

    /// Resolve an exact id or a unique prefix.
    pub fn resolve_session_id(&self, id_or_prefix: &str) -> Result<Option<String>> {
        let normalized = id_or_prefix.trim();
        if normalized.is_empty() {
            return Ok(None);
        }
        if self.get_session_info(normalized)?.is_some() {
            return Ok(Some(normalized.to_string()));
        }
        let matches = self.find_session_ids(normalized, 2)?;
        Ok(if matches.len() == 1 {
            matches.into_iter().next()
        } else {
            None
        })
    }

    pub fn get_session_info(&self, session_id: &str) -> Result<Option<SessionInfo>> {
        Ok(self
            .conn
            .query_row(
                "SELECT s.id, s.created_at, s.updated_at, s.title, s.metadata, COUNT(m.id)
                 FROM sessions s LEFT JOIN messages m ON s.id = m.session_id
                 WHERE s.id = ?1 GROUP BY s.id",
                params![session_id],
                Self::session_from_row,
            )
            .optional()?)
    }

    pub fn search_messages(&self, query: &str, limit: usize) -> Result<Vec<SearchResult>> {
        let mut stmt = self.conn.prepare(
            "SELECT m.session_id, s.title, m.role, m.content, m.timestamp
             FROM messages m JOIN sessions s ON m.session_id = s.id
             WHERE m.content LIKE ?1
             ORDER BY m.timestamp DESC, m.id DESC LIMIT ?2",
        )?;
        let rows = stmt.query_map(params![format!("%{query}%"), limit as i64], |row| {
            Ok(SearchResult {
                session_id: row.get(0)?,
                session_title: row.get(1)?,
                role: row.get(2)?,
                content: row.get(3)?,
                timestamp: row.get::<_, Option<String>>(4)?.unwrap_or_default(),
            })
        })?;
        Ok(rows.collect::<rusqlite::Result<_>>()?)
    }

    pub fn clear_all_history(&self) -> Result<()> {
        self.conn.execute_batch("DELETE FROM messages; DELETE FROM sessions;")?;
        Ok(())
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn history() -> (tempfile::TempDir, ConversationHistory) {
        let dir = tempfile::tempdir().unwrap();
        let history = ConversationHistory::new(&dir.path().join("history")).unwrap();
        (dir, history)
    }

    #[test]
    fn creates_database() {
        let (dir, history) = history();
        assert!(dir.path().join("history").join("conversations.db").exists());
        assert!(history.db_path().ends_with("conversations.db"));
    }

    #[test]
    fn start_session_generates_or_reuses_ids() {
        let (_dir, history) = history();
        let generated = history.start_session(None).unwrap();
        assert_eq!(generated.len(), 36);
        assert_eq!(history.start_session(Some("fixed")).unwrap(), "fixed");
        assert_eq!(history.start_session(Some("fixed")).unwrap(), "fixed");
        assert_eq!(history.list_sessions(50).unwrap().len(), 2);
    }

    #[test]
    fn add_and_get_messages_in_order() {
        let (_dir, history) = history();
        let id = history.start_session(None).unwrap();
        history.add_message(&id, "user", "first").unwrap();
        history.add_message(&id, "assistant", "second").unwrap();
        history.add_message(&id, "user", "third").unwrap();
        let messages = history.get_session_messages(&id).unwrap();
        let contents: Vec<&str> = messages.iter().map(|m| m.content.as_str()).collect();
        assert_eq!(contents, vec!["first", "second", "third"]);
        assert_eq!(messages[1].role, "assistant");
        assert!(messages[0].metadata.is_none());
        assert_eq!(history.get_session_info(&id).unwrap().unwrap().message_count, 3);
    }

    #[test]
    fn recent_messages_are_limited_and_chronological() {
        let (_dir, history) = history();
        let id = history.start_session(None).unwrap();
        for i in 0..25 {
            history.add_message(&id, "user", &format!("m{i}")).unwrap();
        }
        let recent = history.get_recent_messages(&id, 20).unwrap();
        assert_eq!(recent.len(), 20);
        assert_eq!(recent.first().unwrap().1, "m5");
        assert_eq!(recent.last().unwrap().1, "m24");
    }

    #[test]
    fn resolve_session_prefixes() {
        let (_dir, history) = history();
        history.start_session(Some("abc-111")).unwrap();
        history.start_session(Some("abc-222")).unwrap();
        history.start_session(Some("xyz-333")).unwrap();
        assert_eq!(history.resolve_session_id("xyz").unwrap().as_deref(), Some("xyz-333"));
        assert_eq!(
            history.resolve_session_id("abc-111").unwrap().as_deref(),
            Some("abc-111")
        );
        assert_eq!(history.resolve_session_id("abc").unwrap(), None);
        assert_eq!(history.resolve_session_id("  ").unwrap(), None);
        assert_eq!(history.find_session_ids("abc", 10).unwrap().len(), 2);
        assert!(history.find_session_ids("a%", 10).unwrap().is_empty());
    }

    #[test]
    fn search_messages_matches_content() {
        let (_dir, history) = history();
        let id = history.start_session(None).unwrap();
        history.add_message(&id, "user", "how do I use tar").unwrap();
        history.add_message(&id, "assistant", "use tar -xzf").unwrap();
        history.add_message(&id, "user", "something else").unwrap();
        let results = history.search_messages("tar", 50).unwrap();
        assert_eq!(results.len(), 2);
        assert_eq!(results[0].content, "use tar -xzf");
        assert_eq!(results[0].session_id, id);
    }

    #[test]
    fn clear_all_history_removes_everything() {
        let (_dir, history) = history();
        let id = history.start_session(None).unwrap();
        history.add_message(&id, "user", "x").unwrap();
        history.clear_all_history().unwrap();
        assert!(history.list_sessions(50).unwrap().is_empty());
        assert!(history.get_session_messages(&id).unwrap().is_empty());
    }

    #[test]
    fn reads_rows_written_by_python_version() {
        let dir = tempfile::tempdir().unwrap();
        let history_dir = dir.path().join("history");
        std::fs::create_dir_all(&history_dir).unwrap();
        let conn = Connection::open(history_dir.join("conversations.db")).unwrap();
        conn.execute_batch(SCHEMA).unwrap();
        conn.execute(
            "INSERT INTO sessions (id, title, metadata) VALUES ('py-1', NULL, '{\"a\": 1}')",
            [],
        )
        .unwrap();
        conn.execute(
            "INSERT INTO messages (session_id, role, content, metadata) VALUES ('py-1', 'user', 'hi', NULL)",
            [],
        )
        .unwrap();
        drop(conn);
        let history = ConversationHistory::new(&history_dir).unwrap();
        let info = history.get_session_info("py-1").unwrap().unwrap();
        assert_eq!(info.message_count, 1);
        assert_eq!(info.metadata, Some(serde_json::json!({"a": 1})));
        assert_eq!(
            history.get_recent_messages("py-1", 20).unwrap(),
            vec![("user".into(), "hi".into())]
        );
    }
}
