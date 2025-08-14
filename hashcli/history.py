"""Conversation history management with persistent storage."""

import json
import sqlite3
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Union


@dataclass
class Message:
    """Represents a single conversation message."""

    role: str  # 'user', 'assistant', 'system', 'tool'
    content: str
    timestamp: datetime
    metadata: Optional[Dict[str, Any]] = None

    def to_dict(self) -> Dict[str, Any]:
        """Convert message to dictionary format."""
        data = asdict(self)
        data["timestamp"] = self.timestamp.isoformat()
        return data

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Message":
        """Create message from dictionary format."""
        data["timestamp"] = datetime.fromisoformat(data["timestamp"])
        return cls(**data)


class ConversationHistory:
    """Manages persistent conversation history using SQLite."""

    def __init__(self, history_dir: Optional[Path] = None):
        """Initialize history manager with storage directory."""
        if history_dir is None:
            history_dir = Path.home() / ".hashcli" / "history"

        self.history_dir = Path(history_dir)
        self.history_dir.mkdir(parents=True, exist_ok=True)

        self.db_path = self.history_dir / "conversations.db"
        self._init_database()

    def _init_database(self):
        """Initialize SQLite database with required tables."""
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS sessions (
                    id TEXT PRIMARY KEY,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    title TEXT,
                    metadata TEXT  -- JSON
                )
            """
            )

            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS messages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id TEXT NOT NULL,
                    role TEXT NOT NULL,
                    content TEXT NOT NULL,
                    timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    metadata TEXT,  -- JSON
                    FOREIGN KEY (session_id) REFERENCES sessions (id)
                )
            """
            )

            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_messages_session_id 
                ON messages (session_id)
            """
            )

            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_messages_timestamp 
                ON messages (timestamp)
            """
            )

            conn.commit()

    def start_session(
        self, title: Optional[str] = None, metadata: Optional[Dict[str, Any]] = None
    ) -> str:
        """Start a new conversation session."""
        session_id = str(uuid.uuid4())

        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                """
                INSERT INTO sessions (id, title, metadata)
                VALUES (?, ?, ?)
            """,
                (session_id, title, json.dumps(metadata) if metadata else None),
            )
            conn.commit()

        return session_id

    def end_session(self, session_id: str):
        """End a conversation session (update timestamp)."""
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                """
                UPDATE sessions 
                SET updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
            """,
                (session_id,),
            )
            conn.commit()

    def add_message(
        self,
        session_id: str,
        role: str,
        content: str,
        metadata: Optional[Dict[str, Any]] = None,
    ):
        """Add a message to a conversation session."""
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                """
                INSERT INTO messages (session_id, role, content, metadata)
                VALUES (?, ?, ?, ?)
            """,
                (session_id, role, content, json.dumps(metadata) if metadata else None),
            )

            # Update session timestamp
            conn.execute(
                """
                UPDATE sessions 
                SET updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
            """,
                (session_id,),
            )

            conn.commit()

    def get_session_messages(self, session_id: str) -> List[Dict[str, Any]]:
        """Get all messages for a session."""
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.execute(
                """
                SELECT role, content, timestamp, metadata
                FROM messages
                WHERE session_id = ?
                ORDER BY timestamp ASC
            """,
                (session_id,),
            )

            messages = []
            for row in cursor.fetchall():
                message = {
                    "role": row["role"],
                    "content": row["content"],
                    "timestamp": row["timestamp"],
                    "metadata": (
                        json.loads(row["metadata"]) if row["metadata"] else None
                    ),
                }
                messages.append(message)

            return messages

    def get_recent_messages(
        self, session_id: str, limit: int = 20
    ) -> List[Dict[str, str]]:
        """Get recent messages for a session in LLM format."""
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.execute(
                """
                SELECT role, content
                FROM messages
                WHERE session_id = ?
                ORDER BY timestamp DESC
                LIMIT ?
            """,
                (session_id, limit),
            )

            messages = []
            for row in reversed(
                cursor.fetchall()
            ):  # Reverse to get chronological order
                messages.append({"role": row["role"], "content": row["content"]})

            return messages

    def list_sessions(self, limit: int = 50) -> List[Dict[str, Any]]:
        """List recent conversation sessions."""
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.execute(
                """
                SELECT s.id, s.created_at, s.updated_at, s.title,
                       COUNT(m.id) as message_count
                FROM sessions s
                LEFT JOIN messages m ON s.id = m.session_id
                GROUP BY s.id
                ORDER BY s.updated_at DESC
                LIMIT ?
            """,
                (limit,),
            )

            sessions = []
            for row in cursor.fetchall():
                session = {
                    "id": row["id"],
                    "created": row["created_at"],
                    "updated": row["updated_at"],
                    "title": row["title"],
                    "message_count": row["message_count"],
                }
                sessions.append(session)

            return sessions

    def get_session_info(self, session_id: str) -> Optional[Dict[str, Any]]:
        """Get information about a specific session."""
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.execute(
                """
                SELECT s.id, s.created_at, s.updated_at, s.title, s.metadata,
                       COUNT(m.id) as message_count
                FROM sessions s
                LEFT JOIN messages m ON s.id = m.session_id
                WHERE s.id = ?
                GROUP BY s.id
            """,
                (session_id,),
            )

            row = cursor.fetchone()
            if not row:
                return None

            return {
                "id": row["id"],
                "created": row["created_at"],
                "updated": row["updated_at"],
                "title": row["title"],
                "metadata": json.loads(row["metadata"]) if row["metadata"] else None,
                "message_count": row["message_count"],
            }

    def search_messages(self, query: str, limit: int = 50) -> List[Dict[str, Any]]:
        """Search for messages containing a query string."""
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.execute(
                """
                SELECT m.session_id, m.role, m.content, m.timestamp,
                       s.title
                FROM messages m
                JOIN sessions s ON m.session_id = s.id
                WHERE m.content LIKE ?
                ORDER BY m.timestamp DESC
                LIMIT ?
            """,
                (f"%{query}%", limit),
            )

            results = []
            for row in cursor.fetchall():
                result = {
                    "session_id": row["session_id"],
                    "session_title": row["title"],
                    "role": row["role"],
                    "content": row["content"],
                    "timestamp": row["timestamp"],
                }
                results.append(result)

            return results

    def delete_session(self, session_id: str) -> bool:
        """Delete a conversation session and all its messages."""
        try:
            with sqlite3.connect(self.db_path) as conn:
                # Delete messages first
                conn.execute("DELETE FROM messages WHERE session_id = ?", (session_id,))
                # Delete session
                conn.execute("DELETE FROM sessions WHERE id = ?", (session_id,))
                conn.commit()
            return True
        except Exception:
            return False

    def clear_old_history(self, days: int = 30) -> int:
        """Clear history older than specified days. Returns number of deleted sessions."""
        cutoff_date = datetime.now() - timedelta(days=days)

        with sqlite3.connect(self.db_path) as conn:
            # Get sessions to delete
            cursor = conn.execute(
                """
                SELECT id FROM sessions 
                WHERE updated_at < ?
            """,
                (cutoff_date.isoformat(),),
            )

            session_ids = [row[0] for row in cursor.fetchall()]

            if session_ids:
                placeholders = ",".join("?" * len(session_ids))

                # Delete messages
                conn.execute(
                    f"""
                    DELETE FROM messages 
                    WHERE session_id IN ({placeholders})
                """,
                    session_ids,
                )

                # Delete sessions
                conn.execute(
                    f"""
                    DELETE FROM sessions 
                    WHERE id IN ({placeholders})
                """,
                    session_ids,
                )

                conn.commit()

            return len(session_ids)

    def clear_all_history(self) -> bool:
        """Clear all conversation history."""
        try:
            with sqlite3.connect(self.db_path) as conn:
                conn.execute("DELETE FROM messages")
                conn.execute("DELETE FROM sessions")
                conn.commit()
            return True
        except Exception:
            return False

    def get_statistics(self) -> Dict[str, Any]:
        """Get statistics about conversation history."""
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row

            # Total counts
            session_count = conn.execute("SELECT COUNT(*) FROM sessions").fetchone()[0]
            message_count = conn.execute("SELECT COUNT(*) FROM messages").fetchone()[0]

            # Recent activity
            week_ago = (datetime.now() - timedelta(days=7)).isoformat()
            recent_sessions = conn.execute(
                """
                SELECT COUNT(*) FROM sessions 
                WHERE created_at > ?
            """,
                (week_ago,),
            ).fetchone()[0]

            recent_messages = conn.execute(
                """
                SELECT COUNT(*) FROM messages 
                WHERE timestamp > ?
            """,
                (week_ago,),
            ).fetchone()[0]

            # Database size
            db_size = self.db_path.stat().st_size if self.db_path.exists() else 0

            return {
                "total_sessions": session_count,
                "total_messages": message_count,
                "recent_sessions_7d": recent_sessions,
                "recent_messages_7d": recent_messages,
                "database_size_bytes": db_size,
                "database_path": str(self.db_path),
            }

    def export_session(self, session_id: str, format: str = "json") -> Optional[str]:
        """Export a session to various formats."""
        session_info = self.get_session_info(session_id)
        if not session_info:
            return None

        messages = self.get_session_messages(session_id)

        export_data = {
            "session": session_info,
            "messages": messages,
            "exported_at": datetime.now().isoformat(),
        }

        if format.lower() == "json":
            return json.dumps(export_data, indent=2, default=str)
        elif format.lower() == "markdown":
            return self._export_to_markdown(export_data)
        else:
            raise ValueError(f"Unsupported export format: {format}")

    def _export_to_markdown(self, data: Dict[str, Any]) -> str:
        """Export conversation to markdown format."""
        lines = [
            f"# Conversation Export",
            f"",
            f"**Session ID:** {data['session']['id']}",
            f"**Created:** {data['session']['created']}",
            f"**Messages:** {data['session']['message_count']}",
            f"**Exported:** {data['exported_at']}",
            f"",
            f"---",
            f"",
        ]

        for i, message in enumerate(data["messages"], 1):
            role = message["role"].title()
            timestamp = message["timestamp"]
            content = message["content"]

            lines.extend(
                [
                    f"## Message {i} - {role}",
                    f"*{timestamp}*",
                    f"",
                    content,
                    f"",
                    f"---",
                    f"",
                ]
            )

        return "\\n".join(lines)
