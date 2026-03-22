# Book Tracker

Offline-first Streamlit dashboard for managing a personal library, logging reading sessions, and tracking goals. Backed by a local SQLite database.

## Quick Start

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
streamlit run app.py
```

The app creates `book_tracker.db` in the project root on first launch. Seed books/sessions load automatically unless disabled via `SEED_SAMPLE_DATA=0`.

## Configuration

| Variable | Default | Purpose |
| --- | --- | --- |
| `BOOK_TRACKER_DB` | `<repo>/book_tracker.db` | Path to the SQLite database file. Point to persistent storage when deploying. |
| `SEED_SAMPLE_DATA` | `1` | Set to `0` to skip inserting sample books/sessions/goals. Recommended for production. |
| `ADMIN_EMAIL` | `admin@book.local` | Bootstrap admin account email created on first launch. |
| `ADMIN_PASSWORD` | `admin1234` | Bootstrap admin account password created on first launch. Change this in production. |
| `SELF_SIGNUP_ENABLED` | `0` | Set to `1` to show self-signup on the login page. |
| `SIGNUP_INVITE_CODE` | `` | Optional invite code required for self-signup when set. |
| `SIGNUP_EMAIL_ALLOWLIST` | `` | Optional comma-separated email allowlist for self-signup. |

Example:

```bash
BOOK_TRACKER_DB=/data/book_tracker.db SEED_SAMPLE_DATA=0 streamlit run app.py
```

With custom admin credentials:

```bash
BOOK_TRACKER_DB=/data/book_tracker.db \
SEED_SAMPLE_DATA=0 \
ADMIN_EMAIL=admin@example.com \
ADMIN_PASSWORD='strong-password' \
streamlit run app.py
```

Self-signup with guardrails:

```bash
SELF_SIGNUP_ENABLED=1 \
SIGNUP_INVITE_CODE='team-invite-2026' \
SIGNUP_EMAIL_ALLOWLIST='alice@example.com,bob@example.com' \
streamlit run app.py
```

If both `SIGNUP_INVITE_CODE` and `SIGNUP_EMAIL_ALLOWLIST` are set, both checks are enforced.

## Deployment Notes

1. Ensure the target has Python 3.9+ and can install the packages from `requirements.txt`.
2. Provide persistent storage for the SQLite file (e.g., mounted volume). Set `BOOK_TRACKER_DB` accordingly.
3. Disable sample data with `SEED_SAMPLE_DATA=0`.
4. Use the Procfile entry below or run Streamlit manually with `--server.address=0.0.0.0 --server.port=$PORT`.

### Streamlit Community Cloud

1. Push this repo to GitHub.
2. On https://share.streamlit.io, point to `app.py`.
3. In the app settings add secrets (or environment variables):
   ```toml
   BOOK_TRACKER_DB = "book_tracker.db"
   SEED_SAMPLE_DATA = "0"
   ```
4. (Optional) Connect a managed storage volume; otherwise the bundled SQLite file resets when the container sleeps.

## Procfile

For platforms like Render or Heroku:

```
web: streamlit run app.py --server.address=0.0.0.0 --server.port=${PORT:-8501}
```

## Export/Import

The Settings tab lets you export CSV snapshots of books/sessions and import them later. Use this to seed production data once you have live records.

## Admin User Details

Admins can view a full user-details view from the `Admin` tab, including:
- account metadata (`id`, `email`, role, status, created/last-login timestamps, creator email)
- reading aggregates (books, sessions, finished books, pages, hours)
- recent books and sessions for the selected user

## Session Notes

When logging a reading session, you can optionally capture quote details:
- quote page number
- line reference (for example, `Line 4-6`)
- quote text
- multiple quotes in the same session using `Add quote` before `Stop & log session`

## Reading Progress

Each session updates book progress automatically:
- `pages_read` in a session is added to the book's current progress
- library view shows `current_page`, `pages_left`, and `progress_pct`
- when starting the next session for a book, the app shows the next start page based on where you left off
- session form supports `Start page` and `End page`; pages read is auto-calculated

## Tests

Basic syntax check (recommended for CI):

```bash
python -m compileall app.py
```

Streamlit can also run headless for smoke tests:

```bash
streamlit run app.py --server.headless=true --browser.gatherUsageStats=false --server.fileWatcherType=none
```
