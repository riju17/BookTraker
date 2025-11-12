"""Offline-first Book Tracker built with Streamlit and SQLite.

Quick start:
1. pip install streamlit pandas
2. streamlit run app.py
"""

import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

import pandas as pd
import streamlit as st


DEFAULT_DB = Path(__file__).with_name("book_tracker.db")
SHELVES = ["reading", "to_read", "finished"]


def get_setting(key: str, default: str) -> str:
    if key in os.environ and os.environ[key]:
        return os.environ[key]
    if key in st.secrets and st.secrets[key]:
        return str(st.secrets[key])
    return default


DB_PATH = Path(get_setting("BOOK_TRACKER_DB", str(DEFAULT_DB))).expanduser()
SEED_FLAG = get_setting("SEED_SAMPLE_DATA", "1")
SEED_SAMPLE_DATA = str(SEED_FLAG).lower() not in {"0", "false", "no"}


def _utc_now() -> str:
    return datetime.utcnow().isoformat(timespec="seconds")


@st.cache_resource(show_spinner=False)
def get_connection() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
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


def init_db() -> None:
    with get_cursor() as cur:
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS books (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT NOT NULL,
                author TEXT NOT NULL,
                isbn TEXT,
                total_pages INTEGER,
                shelf TEXT NOT NULL DEFAULT 'to_read',
                added_at TEXT NOT NULL
            )
            """
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS sessions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                book_id INTEGER NOT NULL,
                start_ts TEXT NOT NULL,
                end_ts TEXT NOT NULL,
                pages_read INTEGER DEFAULT 0,
                note TEXT,
                FOREIGN KEY(book_id) REFERENCES books(id) ON DELETE CASCADE
            )
            """
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS goals (
                id INTEGER PRIMARY KEY CHECK(id = 1),
                year INTEGER NOT NULL,
                daily_minutes INTEGER NOT NULL DEFAULT 30,
                yearly_books INTEGER NOT NULL DEFAULT 12
            )
            """
        )
    if SEED_SAMPLE_DATA:
        seed_data()


def seed_data() -> None:
    with get_cursor() as cur:
        cur.execute("SELECT COUNT(*) FROM books")
        if cur.fetchone()[0] == 0:
            now = _utc_now()
            sample_books = [
                ("Atomic Habits", "James Clear", "9780735211292", 320, "reading", now),
                ("Project Hail Mary", "Andy Weir", "9780593135204", 496, "to_read", now),
                ("The Pragmatic Programmer", "Andrew Hunt", "9780201616224", 352, "finished", now),
            ]
            cur.executemany(
                """
                INSERT INTO books (title, author, isbn, total_pages, shelf, added_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                sample_books,
            )
        cur.execute("SELECT COUNT(*) FROM sessions")
        if cur.fetchone()[0] == 0:
            cur.execute("SELECT id FROM books WHERE title = ?", ("Atomic Habits",))
            book_id = cur.fetchone()[0]
            start = datetime.utcnow() - timedelta(hours=1)
            end = datetime.utcnow()
            cur.execute(
                """
                INSERT INTO sessions (book_id, start_ts, end_ts, pages_read, note)
                VALUES (?, ?, ?, ?, ?)
                """,
                (book_id, start.isoformat(timespec="seconds"), end.isoformat(timespec="seconds"), 25, "Morning session"),
            )
        cur.execute("SELECT COUNT(*) FROM goals WHERE id = 1")
        if cur.fetchone()[0] == 0:
            cur.execute(
                """
                INSERT INTO goals (id, year, daily_minutes, yearly_books)
                VALUES (1, ?, ?, ?)
                """,
                (datetime.utcnow().year, 30, 24),
            )


def fetch_books(order_by: str = "title") -> List[sqlite3.Row]:
    valid_columns = {"title", "author", "added_at", "shelf"}
    column = order_by if order_by in valid_columns else "title"
    with get_cursor() as cur:
        cur.execute(f"SELECT * FROM books ORDER BY {column} COLLATE NOCASE")
        return cur.fetchall()


def insert_book(data: Dict[str, Any]) -> None:
    with get_cursor() as cur:
        cur.execute(
            """
            INSERT INTO books (title, author, isbn, total_pages, shelf, added_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                data["title"],
                data["author"],
                data.get("isbn"),
                data.get("total_pages"),
                data.get("shelf", "to_read"),
                _utc_now(),
            ),
        )


def update_book(book_id: int, data: Dict[str, Any]) -> None:
    with get_cursor() as cur:
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
                data.get("total_pages"),
                data.get("shelf", "to_read"),
                book_id,
            ),
        )


def remove_book(book_id: int) -> None:
    with get_cursor() as cur:
        cur.execute("DELETE FROM books WHERE id = ?", (book_id,))


def fetch_sessions(limit: Optional[int] = None) -> List[sqlite3.Row]:
    sql = """
        SELECT s.*, b.title, b.author
        FROM sessions s
        JOIN books b ON b.id = s.book_id
        ORDER BY start_ts DESC
    """
    if limit:
        sql += f" LIMIT {int(limit)}"
    with get_cursor() as cur:
        cur.execute(sql)
        return cur.fetchall()


def insert_session(book_id: int, start_ts: str, end_ts: str, pages_read: int, note: str) -> None:
    with get_cursor() as cur:
        cur.execute(
            """
            INSERT INTO sessions (book_id, start_ts, end_ts, pages_read, note)
            VALUES (?, ?, ?, ?, ?)
            """,
            (book_id, start_ts, end_ts, pages_read, note),
        )


def fetch_goals() -> Dict[str, Any]:
    with get_cursor() as cur:
        cur.execute("SELECT * FROM goals WHERE id = 1")
        row = cur.fetchone()
        if not row:
            return {"year": datetime.utcnow().year, "daily_minutes": 30, "yearly_books": 24}
        return dict(row)


def update_goals(year: int, daily_minutes: int, yearly_books: int) -> None:
    with get_cursor() as cur:
        cur.execute(
            """
            INSERT INTO goals (id, year, daily_minutes, yearly_books)
            VALUES (1, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET year = excluded.year,
                                         daily_minutes = excluded.daily_minutes,
                                         yearly_books = excluded.yearly_books
            """,
            (year, daily_minutes, yearly_books),
        )


def ensure_session_state():
    st.session_state.setdefault("active_session", None)


def rows_to_df(rows: List[sqlite3.Row]) -> pd.DataFrame:
    if not rows:
        return pd.DataFrame()
    return pd.DataFrame([dict(row) for row in rows])


def library_tab():
    st.subheader("Library")
    books = fetch_books()
    if not books:
        st.info("No books yet. Add one from the Add Book tab.")
        return

    df = rows_to_df(books)
    shelf_counts = df["shelf"].value_counts().reindex(SHELVES, fill_value=0)
    cols = st.columns(len(SHELVES))
    for col, shelf in zip(cols, SHELVES):
        col.metric(shelf.replace("_", " ").title(), int(shelf_counts[shelf]))

    st.dataframe(
        df[["title", "author", "isbn", "total_pages", "shelf", "added_at"]],
        use_container_width=True,
    )

    with st.expander("Edit or remove a book"):
        book_options = {f"{b['title']} - {b['author']}": b for b in books}
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
                update_book(
                    entry["id"],
                    {"title": title.strip(), "author": author.strip(), "isbn": isbn.strip() or None, "total_pages": pages, "shelf": shelf},
                )
                st.success("Book updated. Refresh the tab to see changes.")
        if st.button("Delete selected book"):
            remove_book(entry["id"])
            st.success("Book removed. Refresh the tab to see the latest list.")


def add_book_tab():
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
                    {"title": title.strip(), "author": author.strip(), "isbn": isbn.strip() or None, "total_pages": pages, "shelf": shelf}
                )
                st.success(f'Added "{title.strip()}".')


def session_tab():
    ensure_session_state()
    st.subheader("Reading Session")
    books = fetch_books(order_by="title")
    if not books:
        st.info("Add a book first.")
        return
    book_lookup = {f"{b['title']} - {b['author']}": b for b in books}
    selected_label = st.selectbox("Book", list(book_lookup.keys()))
    book = book_lookup[selected_label]
    active = st.session_state.active_session

    if not active:
        if st.button("Start session"):
            st.session_state.active_session = {"book_id": book["id"], "start_ts": _utc_now(), "label": selected_label}
            st.success("Session started.")
    else:
        delta = datetime.utcnow() - datetime.fromisoformat(active["start_ts"])
        minutes = int(delta.total_seconds() // 60)
        st.info(f"Active session: {active['label']} - {minutes} minutes elapsed.")
        pages_read = st.number_input("Pages read this session", min_value=0, value=10)
        note = st.text_area("Notes", "")
        if st.button("Stop & log session"):
            end_ts = _utc_now()
            insert_session(active["book_id"], active["start_ts"], end_ts, int(pages_read), note.strip())
            st.session_state.active_session = None
            st.success("Session saved.")
    st.divider()
    st.caption("Recent sessions")
    sessions = fetch_sessions(limit=10)
    if sessions:
        session_df = rows_to_df(sessions)
        session_df["duration_min"] = (
            pd.to_datetime(session_df["end_ts"]) - pd.to_datetime(session_df["start_ts"])
        ).dt.total_seconds() / 60
        st.dataframe(
            session_df[["title", "author", "start_ts", "end_ts", "duration_min", "pages_read", "note"]],
            use_container_width=True,
        )
    else:
        st.write("No sessions logged yet.")


def stats_tab():
    st.subheader("Stats & Insights")
    books = fetch_books()
    sessions = fetch_sessions()
    total_books = len(books)
    reading = len([b for b in books if b["shelf"] == "reading"])
    df_sessions = rows_to_df(sessions)

    total_hours = 0.0
    total_pages = 0
    if not df_sessions.empty:
        durations = pd.to_datetime(df_sessions["end_ts"]) - pd.to_datetime(df_sessions["start_ts"])
        total_hours = durations.dt.total_seconds().sum() / 3600
        total_pages = df_sessions["pages_read"].sum()

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


def goals_tab():
    st.subheader("Goals")
    goals = fetch_goals()
    books = fetch_books()
    sessions = fetch_sessions()

    total_minutes_today = 0
    today = pd.Timestamp.utcnow().date()
    if sessions:
        df = rows_to_df(sessions)
        df["start_ts"] = pd.to_datetime(df["start_ts"])
        today_sessions = df[df["start_ts"].dt.date == today]
        total_minutes_today = (
            (pd.to_datetime(today_sessions["end_ts"]) - today_sessions["start_ts"]).dt.total_seconds().sum() / 60
        )
    finished_books = len([b for b in books if b["shelf"] == "finished"])
    st.metric("Today's minutes", f"{total_minutes_today:.0f}", f"Goal: {goals['daily_minutes']} minutes")
    st.metric(
        "Books finished",
        finished_books,
        f"Goal: {goals['yearly_books']} this year",
    )

    with st.form("goal-form"):
        year = st.number_input("Goal year", min_value=2000, max_value=2100, value=goals["year"])
        daily_minutes = st.number_input("Daily minutes goal", min_value=5, max_value=600, value=goals["daily_minutes"], step=5)
        yearly_books = st.number_input("Yearly books goal", min_value=1, max_value=200, value=goals["yearly_books"])
        submitted = st.form_submit_button("Save goals")
        if submitted:
            update_goals(int(year), int(daily_minutes), int(yearly_books))
            st.success("Goals updated.")


def settings_tab():
    st.subheader("Settings & Data")
    st.write(f"Database path: `{DB_PATH}`")
    books = rows_to_df(fetch_books())
    sessions = rows_to_df(fetch_sessions())

    if not books.empty:
        st.download_button(
            "Export books CSV",
            data=books.to_csv(index=False),
            file_name="books_export.csv",
            mime="text/csv",
        )
    if not sessions.empty:
        st.download_button(
            "Export sessions CSV",
            data=sessions.to_csv(index=False),
            file_name="sessions_export.csv",
            mime="text/csv",
        )

    st.markdown("### Import data")
    import_books = st.file_uploader("Import books CSV", type="csv", key="import_books")
    if import_books is not None:
        df = pd.read_csv(import_books).fillna("")
        imported = 0
        for _, row in df.iterrows():
            insert_book(
                {
                    "title": row.get("title", "").strip() or "Untitled",
                    "author": row.get("author", "").strip() or "Unknown",
                    "isbn": str(row.get("isbn")) if row.get("isbn") else None,
                    "total_pages": int(row.get("total_pages", 0) or 0),
                    "shelf": row.get("shelf") if row.get("shelf") in SHELVES else "to_read",
                }
            )
            imported += 1
        st.success(f"Imported {imported} books.")

    import_sessions = st.file_uploader("Import sessions CSV", type="csv", key="import_sessions")
    if import_sessions is not None:
        df = pd.read_csv(import_sessions)
        imported = 0
        for _, row in df.iterrows():
            try:
                insert_session(
                    int(row["book_id"]),
                    row["start_ts"],
                    row["end_ts"],
                    int(row.get("pages_read", 0)),
                    row.get("note", ""),
                )
                imported += 1
            except Exception:
                continue
        st.success(f"Imported {imported} sessions.")


def main():
    st.set_page_config(page_title="Book Tracker", layout="wide", page_icon="BT")
    init_db()
    tabs = st.tabs(["Library", "Add Book", "Session", "Stats", "Goals", "Settings"])
    with tabs[0]:
        library_tab()
    with tabs[1]:
        add_book_tab()
    with tabs[2]:
        session_tab()
    with tabs[3]:
        stats_tab()
    with tabs[4]:
        goals_tab()
    with tabs[5]:
        settings_tab()


if __name__ == "__main__":
    main()
