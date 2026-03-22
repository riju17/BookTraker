"""Offline-first Book Tracker built with Streamlit and SQLite.

Quick start:
1. pip install -r requirements.txt
2. streamlit run app.py
"""

import hashlib
import hmac
import os
import sqlite3
import time
from contextlib import contextmanager
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd
import streamlit as st


DEFAULT_DB = Path(__file__).with_name("book_tracker.db")
SHELVES = ["reading", "to_read", "finished"]
DEFAULT_ADMIN_EMAIL = "admin@book.local"
DEFAULT_ADMIN_PASSWORD = "admin1234"
PASSWORD_ITERATIONS = 200_000


def get_setting(key: str, default: str) -> str:
    if key in os.environ and os.environ[key]:
        return os.environ[key]
    try:
        if key in st.secrets and st.secrets[key]:
            return str(st.secrets[key])
    except Exception:
        pass
    return default


DB_PATH = Path(get_setting("BOOK_TRACKER_DB", str(DEFAULT_DB))).expanduser()
SEED_FLAG = get_setting("SEED_SAMPLE_DATA", "1")
SEED_SAMPLE_DATA = str(SEED_FLAG).lower() not in {"0", "false", "no"}
SELF_SIGNUP_ENABLED = str(get_setting("SELF_SIGNUP_ENABLED", "0")).lower() in {"1", "true", "yes"}
SIGNUP_INVITE_CODE = get_setting("SIGNUP_INVITE_CODE", "").strip()
SIGNUP_EMAIL_ALLOWLIST = {
    item.strip().lower()
    for item in get_setting("SIGNUP_EMAIL_ALLOWLIST", "").split(",")
    if item.strip()
}


def _utc_now() -> str:
    return datetime.utcnow().isoformat(timespec="seconds")


def _format_elapsed(seconds: int) -> str:
    hours, rem = divmod(max(0, int(seconds)), 3600)
    minutes, secs = divmod(rem, 60)
    return f"{hours:02d}:{minutes:02d}:{secs:02d}"


def _hash_password(password: str, salt: Optional[str] = None) -> str:
    if salt is None:
        salt = os.urandom(16).hex()
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), bytes.fromhex(salt), PASSWORD_ITERATIONS)
    return f"pbkdf2_sha256${PASSWORD_ITERATIONS}${salt}${digest.hex()}"


def _verify_password(stored_hash: str, password: str) -> bool:
    try:
        algo, iter_s, salt, digest = stored_hash.split("$", 3)
        if algo != "pbkdf2_sha256":
            return False
        iterations = int(iter_s)
        check = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), bytes.fromhex(salt), iterations).hex()
        return hmac.compare_digest(check, digest)
    except Exception:
        return False


def can_self_signup(email: str, invite_code: str) -> Tuple[bool, str]:
    if not SELF_SIGNUP_ENABLED:
        return False, "Self-signup is disabled. Contact admin."

    if not SIGNUP_INVITE_CODE and not SIGNUP_EMAIL_ALLOWLIST:
        return False, "Self-signup guardrails are not configured. Contact admin."

    clean_email = email.strip().lower()
    if SIGNUP_EMAIL_ALLOWLIST and clean_email not in SIGNUP_EMAIL_ALLOWLIST:
        return False, "Your email is not allowed for signup."

    if SIGNUP_INVITE_CODE and not hmac.compare_digest(invite_code.strip(), SIGNUP_INVITE_CODE):
        return False, "Invalid invite code."

    return True, ""


@st.cache_resource(show_spinner=False)
def get_connection() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


@contextmanager
def get_cursor():
    conn = get_connection()
    cur = conn.cursor()
    try:
        yield cur
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        cur.close()


def table_exists(table_name: str) -> bool:
    with get_cursor() as cur:
        cur.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name = ?", (table_name,))
        return cur.fetchone() is not None


def column_exists(table_name: str, column_name: str) -> bool:
    with get_cursor() as cur:
        cur.execute(f"PRAGMA table_info({table_name})")
        return any(row["name"] == column_name for row in cur.fetchall())


def create_user(
    email: str,
    password: str,
    role: str = "user",
    is_active: int = 1,
    created_by: Optional[int] = None,
) -> int:
    with get_cursor() as cur:
        cur.execute(
            """
            INSERT INTO users (email, password_hash, role, is_active, created_at, created_by, last_login_at)
            VALUES (?, ?, ?, ?, ?, ?, NULL)
            """,
            (email.lower().strip(), _hash_password(password), role, is_active, _utc_now(), created_by),
        )
        return int(cur.lastrowid)


def fetch_user_by_email(email: str) -> Optional[sqlite3.Row]:
    with get_cursor() as cur:
        cur.execute("SELECT * FROM users WHERE lower(email) = ?", (email.lower().strip(),))
        return cur.fetchone()


def fetch_user_by_id(user_id: int) -> Optional[sqlite3.Row]:
    with get_cursor() as cur:
        cur.execute("SELECT * FROM users WHERE id = ?", (user_id,))
        return cur.fetchone()


def fetch_all_users() -> List[sqlite3.Row]:
    with get_cursor() as cur:
        cur.execute(
            """
            SELECT
                u.id,
                u.email,
                u.role,
                u.is_active,
                u.created_at,
                u.last_login_at,
                creator.email AS created_by_email,
                COALESCE(book_stats.books_count, 0) AS books_count,
                COALESCE(book_stats.finished_books, 0) AS finished_books,
                COALESCE(session_stats.sessions_count, 0) AS sessions_count,
                COALESCE(session_stats.pages_read_total, 0) AS pages_read_total,
                COALESCE(session_stats.hours_logged, 0.0) AS hours_logged
            FROM users u
            LEFT JOIN users creator ON creator.id = u.created_by
            LEFT JOIN (
                SELECT
                    user_id,
                    COUNT(*) AS books_count,
                    SUM(CASE WHEN shelf = 'finished' THEN 1 ELSE 0 END) AS finished_books
                FROM books
                GROUP BY user_id
            ) AS book_stats ON book_stats.user_id = u.id
            LEFT JOIN (
                SELECT
                    user_id,
                    COUNT(*) AS sessions_count,
                    SUM(COALESCE(pages_read, 0)) AS pages_read_total,
                    SUM((julianday(end_ts) - julianday(start_ts)) * 24.0) AS hours_logged
                FROM sessions
                GROUP BY user_id
            ) AS session_stats ON session_stats.user_id = u.id
            ORDER BY u.created_at ASC
            """
        )
        return cur.fetchall()


def reset_user_password(target_user_id: int, new_password: str) -> bool:
    clean = (new_password or "").strip()
    if not clean:
        return False
    with get_cursor() as cur:
        cur.execute(
            "UPDATE users SET password_hash = ? WHERE id = ?",
            (_hash_password(clean), target_user_id),
        )
        return cur.rowcount > 0


def fetch_user_books_for_admin(target_user_id: int, limit: int = 20) -> List[sqlite3.Row]:
    with get_cursor() as cur:
        cur.execute(
            """
            SELECT title, author, shelf, total_pages, added_at
            FROM books
            WHERE user_id = ?
            ORDER BY added_at DESC
            LIMIT ?
            """,
            (target_user_id, limit),
        )
        return cur.fetchall()


def fetch_user_sessions_for_admin(target_user_id: int, limit: int = 20) -> List[sqlite3.Row]:
    with get_cursor() as cur:
        cur.execute(
            """
            SELECT
                s.start_ts,
                s.end_ts,
                s.start_page,
                s.end_page,
                s.pages_read,
                s.note,
                COALESCE(q.quote_count, 0) AS quote_count,
                q.quote_preview,
                b.title,
                b.author
            FROM sessions s
            JOIN books b ON b.id = s.book_id
            LEFT JOIN (
                SELECT
                    session_id,
                    COUNT(*) AS quote_count,
                    GROUP_CONCAT(
                        TRIM(
                            COALESCE('p.' || quote_page || ' ', '') ||
                            COALESCE(quote_line || ' ', '') ||
                            COALESCE(quote_text, '')
                        ),
                        ' | '
                    ) AS quote_preview
                FROM session_quotes
                GROUP BY session_id
            ) q ON q.session_id = s.id
            WHERE s.user_id = ?
            ORDER BY s.start_ts DESC
            LIMIT ?
            """,
            (target_user_id, limit),
        )
        return cur.fetchall()


def ensure_admin_user() -> Tuple[int, bool]:
    admin_email = get_setting("ADMIN_EMAIL", DEFAULT_ADMIN_EMAIL).lower().strip()
    admin_password = get_setting("ADMIN_PASSWORD", DEFAULT_ADMIN_PASSWORD)
    user = fetch_user_by_email(admin_email)
    using_default_password = admin_password == DEFAULT_ADMIN_PASSWORD
    if not user:
        admin_id = create_user(admin_email, admin_password, role="admin", is_active=1)
        return admin_id, using_default_password
    with get_cursor() as cur:
        if user["role"] != "admin":
            cur.execute("UPDATE users SET role = 'admin' WHERE id = ?", (user["id"],))
        if int(user["is_active"]) == 0:
            cur.execute("UPDATE users SET is_active = 1 WHERE id = ?", (user["id"],))
    return int(user["id"]), using_default_password


def migrate_legacy_schema(admin_user_id: int) -> None:
    if table_exists("books") and not column_exists("books", "user_id"):
        with get_cursor() as cur:
            cur.execute("ALTER TABLE books ADD COLUMN user_id INTEGER")
            cur.execute("UPDATE books SET user_id = ? WHERE user_id IS NULL", (admin_user_id,))

    if table_exists("sessions") and not column_exists("sessions", "user_id"):
        with get_cursor() as cur:
            cur.execute("ALTER TABLE sessions ADD COLUMN user_id INTEGER")
            cur.execute(
                """
                UPDATE sessions
                SET user_id = (
                    SELECT b.user_id FROM books b WHERE b.id = sessions.book_id
                )
                WHERE user_id IS NULL
                """
            )
            cur.execute("UPDATE sessions SET user_id = ? WHERE user_id IS NULL", (admin_user_id,))

    if table_exists("goals") and not column_exists("goals", "user_id"):
        with get_cursor() as cur:
            cur.execute("SELECT year, daily_minutes, yearly_books FROM goals LIMIT 1")
            row = cur.fetchone()
            values = row if row else (datetime.utcnow().year, 30, 24)
            cur.execute("DROP TABLE goals")
            cur.execute(
                """
                CREATE TABLE goals (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL UNIQUE,
                    year INTEGER NOT NULL,
                    daily_minutes INTEGER NOT NULL DEFAULT 30,
                    yearly_books INTEGER NOT NULL DEFAULT 12,
                    FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
                )
                """
            )
            cur.execute(
                """
                INSERT INTO goals (user_id, year, daily_minutes, yearly_books)
                VALUES (?, ?, ?, ?)
                """,
                (admin_user_id, values[0], values[1], values[2]),
            )

    with get_cursor() as cur:
        cur.execute("UPDATE books SET user_id = ? WHERE user_id IS NULL", (admin_user_id,))
        cur.execute("UPDATE sessions SET user_id = ? WHERE user_id IS NULL", (admin_user_id,))


def init_db() -> Dict[str, Any]:
    with get_cursor() as cur:
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                email TEXT NOT NULL UNIQUE,
                password_hash TEXT NOT NULL,
                role TEXT NOT NULL DEFAULT 'user' CHECK(role IN ('admin', 'user')),
                is_active INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL,
                created_by INTEGER,
                last_login_at TEXT,
                FOREIGN KEY(created_by) REFERENCES users(id) ON DELETE SET NULL
            )
            """
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS books (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                title TEXT NOT NULL,
                author TEXT NOT NULL,
                isbn TEXT,
                total_pages INTEGER,
                current_page INTEGER NOT NULL DEFAULT 0,
                shelf TEXT NOT NULL DEFAULT 'to_read',
                added_at TEXT NOT NULL,
                FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
            )
            """
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS sessions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                book_id INTEGER NOT NULL,
                start_ts TEXT NOT NULL,
                end_ts TEXT NOT NULL,
                start_page INTEGER,
                end_page INTEGER,
                pages_read INTEGER DEFAULT 0,
                note TEXT,
                quote_page INTEGER,
                quote_line TEXT,
                quote_text TEXT,
                FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE,
                FOREIGN KEY(book_id) REFERENCES books(id) ON DELETE CASCADE
            )
            """
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS session_quotes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id INTEGER NOT NULL,
                quote_page INTEGER,
                quote_line TEXT,
                quote_text TEXT,
                created_at TEXT NOT NULL,
                FOREIGN KEY(session_id) REFERENCES sessions(id) ON DELETE CASCADE
            )
            """
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS goals (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL UNIQUE,
                year INTEGER NOT NULL,
                daily_minutes INTEGER NOT NULL DEFAULT 30,
                yearly_books INTEGER NOT NULL DEFAULT 12,
                FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
            )
            """
        )
        cur.execute("CREATE INDEX IF NOT EXISTS idx_books_user_id ON books(user_id)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_sessions_user_id ON sessions(user_id)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_session_quotes_session_id ON session_quotes(session_id)")

    if not column_exists("users", "created_by"):
        with get_cursor() as cur:
            cur.execute("ALTER TABLE users ADD COLUMN created_by INTEGER")
    if not column_exists("users", "last_login_at"):
        with get_cursor() as cur:
            cur.execute("ALTER TABLE users ADD COLUMN last_login_at TEXT")
    if not column_exists("books", "current_page"):
        with get_cursor() as cur:
            cur.execute("ALTER TABLE books ADD COLUMN current_page INTEGER NOT NULL DEFAULT 0")
            cur.execute(
                """
                UPDATE books
                SET current_page = COALESCE(
                    (SELECT SUM(COALESCE(s.pages_read, 0)) FROM sessions s WHERE s.book_id = books.id),
                    0
                )
                """
            )
            cur.execute(
                """
                UPDATE books
                SET current_page = CASE
                    WHEN total_pages IS NOT NULL AND current_page > total_pages THEN total_pages
                    ELSE current_page
                END
                """
            )
    if not column_exists("sessions", "quote_page"):
        with get_cursor() as cur:
            cur.execute("ALTER TABLE sessions ADD COLUMN quote_page INTEGER")
    if not column_exists("sessions", "start_page"):
        with get_cursor() as cur:
            cur.execute("ALTER TABLE sessions ADD COLUMN start_page INTEGER")
    if not column_exists("sessions", "end_page"):
        with get_cursor() as cur:
            cur.execute("ALTER TABLE sessions ADD COLUMN end_page INTEGER")
    if not column_exists("sessions", "quote_line"):
        with get_cursor() as cur:
            cur.execute("ALTER TABLE sessions ADD COLUMN quote_line TEXT")
    if not column_exists("sessions", "quote_text"):
        with get_cursor() as cur:
            cur.execute("ALTER TABLE sessions ADD COLUMN quote_text TEXT")
    if table_exists("sessions") and table_exists("session_quotes"):
        with get_cursor() as cur:
            cur.execute(
                """
                INSERT INTO session_quotes (session_id, quote_page, quote_line, quote_text, created_at)
                SELECT s.id, s.quote_page, s.quote_line, s.quote_text, COALESCE(s.end_ts, ?)
                FROM sessions s
                WHERE (s.quote_text IS NOT NULL OR s.quote_line IS NOT NULL OR s.quote_page IS NOT NULL)
                  AND NOT EXISTS (
                      SELECT 1 FROM session_quotes q WHERE q.session_id = s.id
                  )
                """,
                (_utc_now(),),
            )
    with get_cursor() as cur:
        cur.execute("CREATE INDEX IF NOT EXISTS idx_users_created_by ON users(created_by)")

    admin_user_id, using_default_admin_password = ensure_admin_user()
    migrate_legacy_schema(admin_user_id)
    if SEED_SAMPLE_DATA:
        seed_data(admin_user_id)

    return {
        "admin_email": get_setting("ADMIN_EMAIL", DEFAULT_ADMIN_EMAIL).lower().strip(),
        "using_default_admin_password": using_default_admin_password,
    }


def seed_data(user_id: int) -> None:
    with get_cursor() as cur:
        cur.execute("SELECT COUNT(*) FROM books WHERE user_id = ?", (user_id,))
        if cur.fetchone()[0] == 0:
            now = _utc_now()
            sample_books = [
                (user_id, "Atomic Habits", "James Clear", "9780735211292", 320, 25, "reading", now),
                (user_id, "Project Hail Mary", "Andy Weir", "9780593135204", 496, 0, "to_read", now),
                (user_id, "The Pragmatic Programmer", "Andrew Hunt", "9780201616224", 352, 352, "finished", now),
            ]
            cur.executemany(
                """
                INSERT INTO books (user_id, title, author, isbn, total_pages, current_page, shelf, added_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                sample_books,
            )

        cur.execute("SELECT COUNT(*) FROM sessions WHERE user_id = ?", (user_id,))
        if cur.fetchone()[0] == 0:
            cur.execute("SELECT id FROM books WHERE user_id = ? AND title = ?", (user_id, "Atomic Habits"))
            row = cur.fetchone()
            if row:
                book_id = row[0]
                start = datetime.utcnow() - timedelta(hours=1)
                end = datetime.utcnow()
                cur.execute(
                    """
                    INSERT INTO sessions (user_id, book_id, start_ts, end_ts, pages_read, note)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (user_id, book_id, start.isoformat(timespec="seconds"), end.isoformat(timespec="seconds"), 25, "Morning session"),
                )

        cur.execute("SELECT COUNT(*) FROM goals WHERE user_id = ?", (user_id,))
        if cur.fetchone()[0] == 0:
            cur.execute(
                """
                INSERT INTO goals (user_id, year, daily_minutes, yearly_books)
                VALUES (?, ?, ?, ?)
                """,
                (user_id, datetime.utcnow().year, 30, 24),
            )


def authenticate(email: str, password: str) -> Optional[sqlite3.Row]:
    user = fetch_user_by_email(email)
    if not user:
        return None
    if int(user["is_active"]) != 1:
        return None
    if not _verify_password(user["password_hash"], password):
        return None
    with get_cursor() as cur:
        cur.execute("UPDATE users SET last_login_at = ? WHERE id = ?", (_utc_now(), user["id"]))
    return user


def fetch_books(user: sqlite3.Row, order_by: str = "title") -> List[sqlite3.Row]:
    valid_columns = {"title", "author", "added_at", "shelf"}
    column = order_by if order_by in valid_columns else "title"
    with get_cursor() as cur:
        if user["role"] == "admin":
            cur.execute(
                f"""
                SELECT b.*, u.email AS owner_email
                FROM books b
                JOIN users u ON u.id = b.user_id
                ORDER BY {column} COLLATE NOCASE
                """
            )
        else:
            cur.execute(
                f"""
                SELECT b.*, u.email AS owner_email
                FROM books b
                JOIN users u ON u.id = b.user_id
                WHERE b.user_id = ?
                ORDER BY {column} COLLATE NOCASE
                """,
                (user["id"],),
            )
        return cur.fetchall()


def fetch_book_by_id(user: sqlite3.Row, book_id: int) -> Optional[sqlite3.Row]:
    with get_cursor() as cur:
        if user["role"] == "admin":
            cur.execute(
                """
                SELECT b.*, u.email AS owner_email
                FROM books b
                JOIN users u ON u.id = b.user_id
                WHERE b.id = ?
                """,
                (book_id,),
            )
        else:
            cur.execute(
                """
                SELECT b.*, u.email AS owner_email
                FROM books b
                JOIN users u ON u.id = b.user_id
                WHERE b.id = ? AND b.user_id = ?
                """,
                (book_id, user["id"]),
            )
        return cur.fetchone()


def insert_book(user: sqlite3.Row, data: Dict[str, Any]) -> None:
    total_pages = data.get("total_pages")
    total_pages = int(total_pages) if total_pages is not None else None
    current_page_raw = data.get("current_page", 0)
    current_page = int(current_page_raw or 0)
    if current_page < 0:
        current_page = 0
    if total_pages is not None:
        if total_pages < 1:
            total_pages = 1
        if current_page > total_pages:
            current_page = total_pages

    with get_cursor() as cur:
        cur.execute(
            """
            INSERT INTO books (user_id, title, author, isbn, total_pages, current_page, shelf, added_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                user["id"],
                data["title"],
                data["author"],
                data.get("isbn"),
                total_pages,
                current_page,
                data.get("shelf", "to_read"),
                _utc_now(),
            ),
        )


def update_book(user: sqlite3.Row, book_id: int, data: Dict[str, Any]) -> bool:
    total_pages = data.get("total_pages")
    total_pages = int(total_pages) if total_pages is not None else None

    with get_cursor() as cur:
        updated = False
        if user["role"] == "admin":
            cur.execute(
                """
                UPDATE books
                SET title = ?, author = ?, isbn = ?, total_pages = ?, shelf = ?
                WHERE id = ?
                """,
                (
                    data["title"],
                    data["author"],
                    data.get("isbn"),
                    total_pages,
                    data.get("shelf", "to_read"),
                    book_id,
                ),
            )
            updated = cur.rowcount > 0
            if total_pages is not None:
                cur.execute(
                    """
                    UPDATE books
                    SET current_page = CASE WHEN current_page > ? THEN ? ELSE current_page END
                    WHERE id = ?
                    """,
                    (total_pages, total_pages, book_id),
                )
        else:
            cur.execute(
                """
                UPDATE books
                SET title = ?, author = ?, isbn = ?, total_pages = ?, shelf = ?
                WHERE id = ? AND user_id = ?
                """,
                (
                    data["title"],
                    data["author"],
                    data.get("isbn"),
                    total_pages,
                    data.get("shelf", "to_read"),
                    book_id,
                    user["id"],
                ),
            )
            updated = cur.rowcount > 0
            if total_pages is not None:
                cur.execute(
                    """
                    UPDATE books
                    SET current_page = CASE WHEN current_page > ? THEN ? ELSE current_page END
                    WHERE id = ? AND user_id = ?
                    """,
                    (total_pages, total_pages, book_id, user["id"]),
                )
        return updated


def remove_book(user: sqlite3.Row, book_id: int) -> bool:
    with get_cursor() as cur:
        if user["role"] == "admin":
            cur.execute("DELETE FROM books WHERE id = ?", (book_id,))
        else:
            cur.execute("DELETE FROM books WHERE id = ? AND user_id = ?", (book_id, user["id"]))
        return cur.rowcount > 0


def fetch_sessions(user: sqlite3.Row, limit: Optional[int] = None) -> List[sqlite3.Row]:
    sql = """
        SELECT
            s.*,
            b.title,
            b.author,
            u.email AS owner_email,
            COALESCE(q.quote_count, 0) AS quote_count,
            q.quote_preview
        FROM sessions s
        JOIN books b ON b.id = s.book_id
        JOIN users u ON u.id = s.user_id
        LEFT JOIN (
            SELECT
                session_id,
                COUNT(*) AS quote_count,
                GROUP_CONCAT(
                    TRIM(
                        COALESCE('p.' || quote_page || ' ', '') ||
                        COALESCE(quote_line || ' ', '') ||
                        COALESCE(quote_text, '')
                    ),
                    ' | '
                ) AS quote_preview
            FROM session_quotes
            GROUP BY session_id
        ) q ON q.session_id = s.id
    """
    params: List[Any] = []
    if user["role"] != "admin":
        sql += " WHERE s.user_id = ?"
        params.append(user["id"])
    sql += " ORDER BY start_ts DESC"
    if limit:
        sql += " LIMIT ?"
        params.append(int(limit))
    with get_cursor() as cur:
        cur.execute(sql, tuple(params))
        return cur.fetchall()


def fetch_quotes_for_sessions(user: sqlite3.Row, session_ids: List[int]) -> Dict[int, List[Dict[str, Any]]]:
    if not session_ids:
        return {}

    placeholders = ",".join("?" for _ in session_ids)
    params: List[Any] = list(session_ids)
    sql = f"""
        SELECT q.session_id, q.quote_page, q.quote_line, q.quote_text
        FROM session_quotes q
        JOIN sessions s ON s.id = q.session_id
        WHERE q.session_id IN ({placeholders})
    """
    if user["role"] != "admin":
        sql += " AND s.user_id = ?"
        params.append(user["id"])
    sql += " ORDER BY q.session_id ASC, q.id ASC"

    quotes_map: Dict[int, List[Dict[str, Any]]] = {sid: [] for sid in session_ids}
    with get_cursor() as cur:
        cur.execute(sql, tuple(params))
        rows = cur.fetchall()
        for row in rows:
            sid = int(row["session_id"])
            quotes_map.setdefault(sid, []).append(
                {
                    "quote_page": row["quote_page"],
                    "quote_line": row["quote_line"],
                    "quote_text": row["quote_text"],
                }
            )
    return quotes_map


def insert_session(
    user: sqlite3.Row,
    book_id: int,
    start_ts: str,
    end_ts: str,
    pages_read: int,
    note: str,
    start_page: Optional[int] = None,
    end_page: Optional[int] = None,
    quote_page: Optional[int] = None,
    quote_line: str = "",
    quote_text: str = "",
    quotes: Optional[List[Dict[str, Any]]] = None,
) -> bool:
    with get_cursor() as cur:
        if user["role"] == "admin":
            cur.execute("SELECT user_id, current_page, total_pages FROM books WHERE id = ?", (book_id,))
        else:
            cur.execute(
                "SELECT user_id, current_page, total_pages FROM books WHERE id = ? AND user_id = ?",
                (book_id, user["id"]),
            )
        row = cur.fetchone()
        if not row:
            return False
        owner_user_id = int(row["user_id"])
        current_page = int(row["current_page"] or 0)
        total_pages = int(row["total_pages"]) if row["total_pages"] is not None else None
        requested_pages = max(0, int(pages_read))
        if total_pages is not None:
            remaining = max(total_pages - current_page, 0)
            pages_logged = min(requested_pages, remaining)
            new_current_page = current_page + pages_logged
        else:
            pages_logged = requested_pages
            new_current_page = current_page + pages_logged

        cur.execute(
            """
            INSERT INTO sessions (
                user_id, book_id, start_ts, end_ts, start_page, end_page, pages_read, note, quote_page, quote_line, quote_text
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                owner_user_id,
                book_id,
                start_ts,
                end_ts,
                start_page,
                end_page,
                pages_logged,
                note,
                quote_page,
                quote_line.strip() or None,
                quote_text.strip() or None,
            ),
        )
        session_id = int(cur.lastrowid)
        cur.execute("UPDATE books SET current_page = ? WHERE id = ?", (new_current_page, book_id))

        quote_items: List[Dict[str, Any]] = []
        if quotes:
            quote_items.extend(quotes)
        elif quote_page is not None or quote_line.strip() or quote_text.strip():
            quote_items.append(
                {"quote_page": quote_page, "quote_line": quote_line, "quote_text": quote_text}
            )

        for item in quote_items:
            page_raw = item.get("quote_page")
            try:
                page = int(page_raw) if page_raw is not None else None
            except Exception:
                page = None
            line = str(item.get("quote_line", "")).strip()
            text = str(item.get("quote_text", "")).strip()
            if not (page is not None or line or text):
                continue
            cur.execute(
                """
                INSERT INTO session_quotes (session_id, quote_page, quote_line, quote_text, created_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (session_id, page, line or None, text or None, _utc_now()),
            )
        return True


def fetch_goals(user: sqlite3.Row) -> Dict[str, Any]:
    with get_cursor() as cur:
        cur.execute("SELECT * FROM goals WHERE user_id = ?", (user["id"],))
        row = cur.fetchone()
        if not row:
            return {"year": datetime.utcnow().year, "daily_minutes": 30, "yearly_books": 24}
        return dict(row)


def update_goals(user: sqlite3.Row, year: int, daily_minutes: int, yearly_books: int) -> None:
    with get_cursor() as cur:
        cur.execute(
            """
            INSERT INTO goals (user_id, year, daily_minutes, yearly_books)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET year = excluded.year,
                                              daily_minutes = excluded.daily_minutes,
                                              yearly_books = excluded.yearly_books
            """,
            (user["id"], year, daily_minutes, yearly_books),
        )


def ensure_session_state():
    st.session_state.setdefault("active_session", None)
    st.session_state.setdefault("user_id", None)
    st.session_state.setdefault("session_quote_drafts", [])


def rows_to_df(rows: List[sqlite3.Row]) -> pd.DataFrame:
    if not rows:
        return pd.DataFrame()
    return pd.DataFrame([dict(row) for row in rows])


def auth_gate(auth_info: Dict[str, Any]) -> Optional[sqlite3.Row]:
    ensure_session_state()
    if st.session_state.user_id:
        user = fetch_user_by_id(int(st.session_state.user_id))
        if user and int(user["is_active"]) == 1:
            return user
        st.session_state.user_id = None

    st.title("Book Tracker")
    if SELF_SIGNUP_ENABLED:
        auth_tabs = st.tabs(["Log in", "Create account"])
        with auth_tabs[0]:
            with st.form("login-form"):
                email = st.text_input("Email")
                password = st.text_input("Password", type="password")
                submitted = st.form_submit_button("Log in")
                if submitted:
                    user = authenticate(email, password)
                    if user:
                        st.session_state.user_id = int(user["id"])
                        st.session_state.active_session = None
                        st.rerun()
                    else:
                        st.error("Invalid credentials or inactive account.")

        with auth_tabs[1]:
            with st.form("signup-form"):
                signup_email = st.text_input("Email", key="signup_email")
                signup_password = st.text_input("Password", type="password", key="signup_password")
                invite_code = st.text_input("Invite code", type="password", key="signup_invite")
                signup_submitted = st.form_submit_button("Create account")
                if signup_submitted:
                    if not signup_email.strip() or not signup_password:
                        st.error("Email and password are required.")
                    elif fetch_user_by_email(signup_email):
                        st.error("An account with this email already exists.")
                    else:
                        allowed, reason = can_self_signup(signup_email, invite_code)
                        if not allowed:
                            st.error(reason)
                        else:
                            create_user(
                                email=signup_email.strip().lower(),
                                password=signup_password,
                                role="user",
                                is_active=1,
                                created_by=None,
                            )
                            st.success("Account created. Use the Log in tab to sign in.")
    else:
        st.subheader("Log in")
        with st.form("login-form"):
            email = st.text_input("Email")
            password = st.text_input("Password", type="password")
            submitted = st.form_submit_button("Log in")
            if submitted:
                user = authenticate(email, password)
                if user:
                    st.session_state.user_id = int(user["id"])
                    st.session_state.active_session = None
                    st.rerun()
                else:
                    st.error("Invalid credentials or inactive account.")

    if auth_info.get("using_default_admin_password"):
        st.warning(
            f"Default admin credentials are active. Email: {auth_info['admin_email']} | Password: {DEFAULT_ADMIN_PASSWORD}. "
            "Set ADMIN_PASSWORD in environment/secrets immediately."
        )
    elif not SELF_SIGNUP_ENABLED:
        st.caption("Self-signup is disabled. Ask admin to create your account.")

    return None


def sidebar_account(user: sqlite3.Row) -> None:
    st.sidebar.subheader("Account")
    st.sidebar.write(f"{user['email']} ({user['role']})")
    if st.sidebar.button("Logout"):
        st.session_state.user_id = None
        st.session_state.active_session = None
        st.rerun()


def library_tab(user: sqlite3.Row):
    st.subheader("Library")
    books = fetch_books(user)
    if not books:
        st.info("No books yet. Add one from the Add Book tab.")
        return

    df = rows_to_df(books)
    df["current_page"] = pd.to_numeric(df.get("current_page"), errors="coerce").fillna(0).astype(int)
    df["total_pages"] = pd.to_numeric(df.get("total_pages"), errors="coerce")
    df["pages_left"] = (df["total_pages"] - df["current_page"]).clip(lower=0)
    df["progress_pct"] = (
        (df["current_page"] / df["total_pages"].replace({0: pd.NA})) * 100
    ).fillna(0).round(1)
    shelf_counts = df["shelf"].value_counts().reindex(SHELVES, fill_value=0)
    cols = st.columns(len(SHELVES))
    for col, shelf in zip(cols, SHELVES):
        col.metric(shelf.replace("_", " ").title(), int(shelf_counts[shelf]))

    columns = ["title", "author", "isbn", "current_page", "total_pages", "pages_left", "progress_pct", "shelf", "added_at"]
    if user["role"] == "admin":
        columns.append("owner_email")

    st.dataframe(df[columns], use_container_width=True)

    with st.expander("Edit or remove a book"):
        book_options = {f"{b['title']} - {b['author']} ({b['owner_email']})": b for b in books}
        selected = st.selectbox("Choose a book", list(book_options.keys()))
        entry = book_options[selected]
        with st.form("edit-book"):
            title = st.text_input("Title", entry["title"])
            author = st.text_input("Author", entry["author"])
            isbn = st.text_input("ISBN", entry["isbn"] or "")
            pages = st.number_input("Total pages", min_value=1, step=1, value=entry["total_pages"] or 1)
            shelf = st.selectbox("Shelf", SHELVES, index=SHELVES.index(entry["shelf"]))
            submitted = st.form_submit_button("Save changes")
            if submitted:
                ok = update_book(
                    user,
                    entry["id"],
                    {"title": title.strip(), "author": author.strip(), "isbn": isbn.strip() or None, "total_pages": pages, "shelf": shelf},
                )
                if ok:
                    st.success("Book updated. Refresh the tab to see changes.")
                else:
                    st.error("Not allowed to update this book.")
        if st.button("Delete selected book"):
            ok = remove_book(user, entry["id"])
            if ok:
                st.success("Book removed. Refresh the tab to see the latest list.")
            else:
                st.error("Not allowed to delete this book.")


def add_book_tab(user: sqlite3.Row):
    st.subheader("Add Book")
    with st.form("add-book"):
        title = st.text_input("Title *")
        author = st.text_input("Author *")
        isbn = st.text_input("ISBN")
        pages = st.number_input("Total pages", min_value=1, value=250)
        shelf = st.selectbox("Shelf", SHELVES, index=1)
        submitted = st.form_submit_button("Add book")
        if submitted:
            if not title.strip() or not author.strip():
                st.error("Title and Author are required.")
            else:
                insert_book(
                    user,
                    {"title": title.strip(), "author": author.strip(), "isbn": isbn.strip() or None, "total_pages": pages, "shelf": shelf},
                )
                st.success(f'Added "{title.strip()}".')


def session_tab(user: sqlite3.Row):
    ensure_session_state()
    st.subheader("Reading Session")
    books = fetch_books(user, order_by="title")
    if not books:
        st.info("Add a book first.")
        return

    book_lookup = {f"{b['title']} - {b['author']} ({b['owner_email']})": b for b in books}
    selected_label = st.selectbox("Book", list(book_lookup.keys()))
    book = book_lookup[selected_label]
    active = st.session_state.active_session

    selected_total_pages = int(book["total_pages"]) if book["total_pages"] is not None else None
    selected_current_page = int(book["current_page"] or 0)
    selected_next_page = selected_current_page + 1
    if selected_total_pages is not None:
        selected_next_page = min(selected_next_page, selected_total_pages)
        selected_remaining = max(selected_total_pages - selected_current_page, 0)
        st.caption(
            f"Progress: {selected_current_page}/{selected_total_pages} pages read. "
            f"Next start page: {selected_next_page}. Remaining: {selected_remaining}."
        )
    else:
        st.caption(f"Progress: {selected_current_page} pages read. Next start page: {selected_next_page}.")

    if active and active.get("user_id") != user["id"]:
        st.session_state.active_session = None
        st.session_state.session_quote_drafts = []
        active = None

    if not active:
        if st.button("Start session"):
            st.session_state.active_session = {
                "book_id": book["id"],
                "start_ts": _utc_now(),
                "label": selected_label,
                "user_id": int(user["id"]),
            }
            st.session_state.session_quote_drafts = []
            st.rerun()
    else:
        active_book = fetch_book_by_id(user, int(active["book_id"]))
        if not active_book:
            st.session_state.active_session = None
            st.session_state.session_quote_drafts = []
            st.error("Active book not found or inaccessible.")
            st.rerun()
        delta = datetime.utcnow() - datetime.fromisoformat(active["start_ts"])
        elapsed_seconds = int(delta.total_seconds())
        st.info(f"Active session: {active['label']} - {_format_elapsed(elapsed_seconds)} elapsed.")
        active_current_page = int(active_book["current_page"] or 0)
        active_total_pages = int(active_book["total_pages"]) if active_book["total_pages"] is not None else None
        active_next_page = active_current_page + 1
        if active_total_pages is not None:
            active_next_page = min(active_next_page, active_total_pages)
            active_remaining = max(active_total_pages - active_current_page, 0)
            st.caption(
                f"This session starts from page {active_next_page}. "
                f"Remaining pages before this session: {active_remaining}."
            )
            if active_remaining > 0:
                start_page = st.number_input(
                    "Start page",
                    min_value=active_next_page,
                    max_value=active_total_pages,
                    value=active_next_page,
                    step=1,
                )
                end_page = st.number_input(
                    "End page",
                    min_value=int(start_page),
                    max_value=active_total_pages,
                    value=int(start_page),
                    step=1,
                )
                pages_read = int(end_page) - int(start_page) + 1
            else:
                st.info("No pages remaining for this book.")
                start_page = active_total_pages
                end_page = active_total_pages
                pages_read = 0
        else:
            st.caption(f"This session starts from page {active_next_page}.")
            start_page = st.number_input(
                "Start page",
                min_value=1,
                value=max(1, active_next_page),
                step=1,
            )
            end_page = st.number_input(
                "End page",
                min_value=int(start_page),
                value=int(start_page),
                step=1,
            )
            pages_read = int(end_page) - int(start_page) + 1
        st.caption(f"Pages read in this session (auto): {pages_read}")
        note = st.text_area("Notes", "")
        st.markdown("**Quote capture (optional)**")
        with st.form("quote-add-form", clear_on_submit=True):
            quote_page = st.number_input("Quote page number", min_value=1, step=1, value=1)
            quote_line = st.text_input("Quote line reference (e.g., Line 4-6)")
            quote_text = st.text_area("Quote text")
            add_quote_clicked = st.form_submit_button("Add quote")
        c1, c2 = st.columns(2)
        remove_last_quote_clicked = c1.button("Remove last quote")
        stop_clicked = c2.button("Stop & log session")

        if add_quote_clicked:
            if quote_line.strip() or quote_text.strip():
                st.session_state.session_quote_drafts.append(
                    {
                        "quote_page": int(quote_page),
                        "quote_line": quote_line.strip(),
                        "quote_text": quote_text.strip(),
                    }
                )
                st.rerun()
            else:
                st.warning("Enter quote line or quote text before adding.")

        if remove_last_quote_clicked and st.session_state.session_quote_drafts:
            st.session_state.session_quote_drafts.pop()
            st.rerun()

        if st.session_state.session_quote_drafts:
            st.caption(f"Quotes queued in this session: {len(st.session_state.session_quote_drafts)}")
            st.dataframe(pd.DataFrame(st.session_state.session_quote_drafts), use_container_width=True)

        if stop_clicked:
            end_ts = _utc_now()
            quotes_payload = list(st.session_state.session_quote_drafts)
            ok = insert_session(
                user,
                active["book_id"],
                active["start_ts"],
                end_ts,
                int(pages_read),
                note.strip(),
                start_page=int(start_page) if pages_read > 0 else None,
                end_page=int(end_page) if pages_read > 0 else None,
                quotes=quotes_payload,
            )
            st.session_state.active_session = None
            st.session_state.session_quote_drafts = []
            if ok:
                st.success("Session saved.")
            else:
                st.error("Could not save session for this book.")
        elif not add_quote_clicked and not remove_last_quote_clicked:
            # Live ticker: refresh the page every second while a session is active.
            time.sleep(1)
            st.rerun()

    st.divider()
    st.caption("Recent sessions")
    sessions = fetch_sessions(user, limit=10)
    if sessions:
        session_df = rows_to_df(sessions)
        session_df["duration_min"] = (pd.to_datetime(session_df["end_ts"]) - pd.to_datetime(session_df["start_ts"])).dt.total_seconds() / 60
        columns = [
            "title",
            "author",
            "start_ts",
            "end_ts",
            "start_page",
            "end_page",
            "duration_min",
            "pages_read",
            "note",
            "quote_count",
            "quote_preview",
        ]
        if user["role"] == "admin":
            columns.append("owner_email")
        st.dataframe(session_df[columns], use_container_width=True)

        session_ids = [int(row["id"]) for row in sessions]
        quotes_map = fetch_quotes_for_sessions(user, session_ids)
        st.markdown("#### View Full Quotes")
        for row in sessions:
            sid = int(row["id"])
            quotes = quotes_map.get(sid, [])
            expander_label = f"{row['title']} | {row['start_ts']} | quotes: {len(quotes)}"
            with st.expander(expander_label):
                if row["note"]:
                    st.write(f"Note: {row['note']}")
                else:
                    st.write("Note: (none)")
                if quotes:
                    st.dataframe(pd.DataFrame(quotes), use_container_width=True)
                else:
                    st.write("No quotes captured in this session.")
    else:
        st.write("No sessions logged yet.")


def stats_tab(user: sqlite3.Row):
    st.subheader("Stats & Insights")
    books = fetch_books(user)
    sessions = fetch_sessions(user)
    total_books = len(books)
    reading = len([b for b in books if b["shelf"] == "reading"])
    df_sessions = rows_to_df(sessions)

    total_hours = 0.0
    total_pages = 0
    if not df_sessions.empty:
        durations = pd.to_datetime(df_sessions["end_ts"]) - pd.to_datetime(df_sessions["start_ts"])
        total_hours = durations.dt.total_seconds().sum() / 3600
        total_pages = int(df_sessions["pages_read"].sum())

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Books in library", total_books)
    c2.metric("Currently reading", reading)
    c3.metric("Hours logged", f"{total_hours:.1f}")
    c4.metric("Pages logged", total_pages)

    if df_sessions.empty:
        st.info("Start logging sessions to unlock charts.")
        return

    df_sessions["month"] = pd.to_datetime(df_sessions["start_ts"]).dt.to_period("M").dt.to_timestamp()
    monthly_pages = df_sessions.groupby("month")["pages_read"].sum().reset_index()
    monthly_hours = (
        df_sessions.assign(duration=lambda d: (pd.to_datetime(d["end_ts"]) - pd.to_datetime(d["start_ts"])).dt.total_seconds() / 3600)
        .groupby("month")["duration"]
        .sum()
        .reset_index()
    )

    st.markdown("**Pages by month**")
    st.bar_chart(monthly_pages.set_index("month"))
    st.markdown("**Hours by month**")
    st.line_chart(monthly_hours.set_index("month"))


def goals_tab(user: sqlite3.Row):
    st.subheader("Goals")
    goals = fetch_goals(user)
    books = fetch_books(user)
    sessions = fetch_sessions(user)

    total_minutes_today = 0.0
    today = pd.Timestamp.utcnow().date()
    if sessions:
        df = rows_to_df(sessions)
        df["start_ts"] = pd.to_datetime(df["start_ts"])
        today_sessions = df[df["start_ts"].dt.date == today]
        total_minutes_today = ((pd.to_datetime(today_sessions["end_ts"]) - today_sessions["start_ts"]).dt.total_seconds().sum() / 60)

    finished_books = len([b for b in books if b["shelf"] == "finished"])
    st.metric("Today's minutes", f"{total_minutes_today:.0f}", f"Goal: {goals['daily_minutes']} minutes")
    st.metric("Books finished", finished_books, f"Goal: {goals['yearly_books']} this year")

    with st.form("goal-form"):
        year = st.number_input("Goal year", min_value=2000, max_value=2100, value=int(goals["year"]))
        daily_minutes = st.number_input("Daily minutes goal", min_value=5, max_value=600, value=int(goals["daily_minutes"]), step=5)
        yearly_books = st.number_input("Yearly books goal", min_value=1, max_value=200, value=int(goals["yearly_books"]))
        submitted = st.form_submit_button("Save goals")
        if submitted:
            update_goals(user, int(year), int(daily_minutes), int(yearly_books))
            st.success("Goals updated.")


def settings_tab(user: sqlite3.Row):
    st.subheader("Settings & Data")
    st.write(f"Database path: `{DB_PATH}`")
    books = rows_to_df(fetch_books(user))
    sessions = rows_to_df(fetch_sessions(user))

    if not books.empty:
        st.download_button("Export books CSV", data=books.to_csv(index=False), file_name="books_export.csv", mime="text/csv")
    if not sessions.empty:
        st.download_button("Export sessions CSV", data=sessions.to_csv(index=False), file_name="sessions_export.csv", mime="text/csv")

    st.markdown("### Import data")
    import_books = st.file_uploader("Import books CSV", type="csv", key="import_books")
    if import_books is not None:
        df = pd.read_csv(import_books).fillna("")
        imported = 0
        for _, row in df.iterrows():
            insert_book(
                user,
                {
                    "title": row.get("title", "").strip() or "Untitled",
                    "author": row.get("author", "").strip() or "Unknown",
                    "isbn": str(row.get("isbn")) if row.get("isbn") else None,
                    "total_pages": int(row.get("total_pages", 0) or 0) or None,
                    "current_page": int(row.get("current_page", 0) or 0),
                    "shelf": row.get("shelf") if row.get("shelf") in SHELVES else "to_read",
                },
            )
            imported += 1
        st.success(f"Imported {imported} books.")

    import_sessions = st.file_uploader("Import sessions CSV", type="csv", key="import_sessions")
    if import_sessions is not None:
        df = pd.read_csv(import_sessions)
        imported = 0
        for _, row in df.iterrows():
            try:
                ok = insert_session(
                    user,
                    int(row["book_id"]),
                    row["start_ts"],
                    row["end_ts"],
                    int(row.get("pages_read", 0)),
                    row.get("note", ""),
                    start_page=(int(row["start_page"]) if "start_page" in row and pd.notna(row["start_page"]) else None),
                    end_page=(int(row["end_page"]) if "end_page" in row and pd.notna(row["end_page"]) else None),
                    quote_page=(int(row["quote_page"]) if "quote_page" in row and pd.notna(row["quote_page"]) else None),
                    quote_line=(str(row["quote_line"]) if "quote_line" in row and pd.notna(row["quote_line"]) else ""),
                    quote_text=(str(row["quote_text"]) if "quote_text" in row and pd.notna(row["quote_text"]) else ""),
                )
                if ok:
                    imported += 1
            except Exception:
                continue
        st.success(f"Imported {imported} sessions.")


def admin_tab(user: sqlite3.Row):
    if user["role"] != "admin":
        st.error("Admin access required.")
        return

    st.subheader("Admin")
    users = rows_to_df(fetch_all_users())
    if not users.empty:
        users["hours_logged"] = users["hours_logged"].round(2)
        st.markdown("### Users overview")
        st.dataframe(
            users[
                [
                    "id",
                    "email",
                    "role",
                    "is_active",
                    "created_at",
                    "last_login_at",
                    "created_by_email",
                    "books_count",
                    "sessions_count",
                    "finished_books",
                    "pages_read_total",
                    "hours_logged",
                ]
            ],
            use_container_width=True,
        )

        st.markdown("### Selected user details")
        option_map = {f"{row['email']} (id: {int(row['id'])})": int(row["id"]) for _, row in users.iterrows()}
        selected_label = st.selectbox("Choose a user", list(option_map.keys()))
        selected_user_id = option_map[selected_label]
        selected = users[users["id"] == selected_user_id].iloc[0]

        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Books", int(selected["books_count"]))
        c2.metric("Sessions", int(selected["sessions_count"]))
        c3.metric("Finished books", int(selected["finished_books"]))
        c4.metric("Pages read", int(selected["pages_read_total"]))
        st.caption(f"Hours logged: {float(selected['hours_logged']):.2f}")

        detail_payload = {
            "id": int(selected["id"]),
            "email": selected["email"],
            "role": selected["role"],
            "is_active": bool(int(selected["is_active"])),
            "created_at": selected["created_at"],
            "last_login_at": selected["last_login_at"] or "Never",
            "created_by": selected["created_by_email"] or "System",
        }
        st.json(detail_payload)

        books_df = rows_to_df(fetch_user_books_for_admin(selected_user_id))
        sessions_df = rows_to_df(fetch_user_sessions_for_admin(selected_user_id))

        st.markdown("#### Recent books")
        if books_df.empty:
            st.write("No books for this user.")
        else:
            st.dataframe(books_df, use_container_width=True)

        st.markdown("#### Recent sessions")
        if sessions_df.empty:
            st.write("No sessions for this user.")
        else:
            sessions_df["duration_min"] = (
                pd.to_datetime(sessions_df["end_ts"]) - pd.to_datetime(sessions_df["start_ts"])
            ).dt.total_seconds() / 60
            st.dataframe(
                sessions_df[
                    [
                        "title",
                        "author",
                        "start_ts",
                        "end_ts",
                        "start_page",
                        "end_page",
                        "duration_min",
                        "pages_read",
                        "note",
                        "quote_count",
                        "quote_preview",
                    ]
                ],
                use_container_width=True,
            )

    st.markdown("### Create user")
    with st.form("create-user-form"):
        email = st.text_input("Email *")
        password = st.text_input("Password *", type="password")
        submitted = st.form_submit_button("Create user")
        if submitted:
            if not email.strip() or not password:
                st.error("Email and password are required.")
            elif fetch_user_by_email(email):
                st.error("User already exists.")
            else:
                create_user(
                    email=email.strip().lower(),
                    password=password,
                    role="user",
                    is_active=1,
                    created_by=int(user["id"]),
                )
                st.success("User created.")

    if not users.empty:
        st.markdown("### Reset user password")
        reset_options = {f"{row['email']} (id: {int(row['id'])})": int(row["id"]) for _, row in users.iterrows()}
        with st.form("reset-password-form"):
            reset_target = st.selectbox("User", list(reset_options.keys()))
            new_password = st.text_input("New password *", type="password")
            confirm_password = st.text_input("Confirm new password *", type="password")
            reset_submitted = st.form_submit_button("Reset password")
            if reset_submitted:
                if not new_password:
                    st.error("New password is required.")
                elif new_password != confirm_password:
                    st.error("Passwords do not match.")
                else:
                    ok = reset_user_password(reset_options[reset_target], new_password)
                    if ok:
                        st.success("Password reset successfully.")
                    else:
                        st.error("Could not reset password.")


def main():
    st.set_page_config(page_title="Book Tracker", layout="wide", page_icon="BT")
    auth_info = init_db()
    user = auth_gate(auth_info)
    if not user:
        return

    sidebar_account(user)

    labels = ["Library", "Add Book", "Session", "Stats", "Goals", "Settings"]
    if user["role"] == "admin":
        labels.append("Admin")

    tabs = st.tabs(labels)
    with tabs[0]:
        library_tab(user)
    with tabs[1]:
        add_book_tab(user)
    with tabs[2]:
        session_tab(user)
    with tabs[3]:
        stats_tab(user)
    with tabs[4]:
        goals_tab(user)
    with tabs[5]:
        settings_tab(user)
    if user["role"] == "admin":
        with tabs[6]:
            admin_tab(user)


if __name__ == "__main__":
    main()
