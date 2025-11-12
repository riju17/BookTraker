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

Example:

```bash
BOOK_TRACKER_DB=/data/book_tracker.db SEED_SAMPLE_DATA=0 streamlit run app.py
```

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

## Tests

Basic syntax check (recommended for CI):

```bash
python -m compileall app.py
```

Streamlit can also run headless for smoke tests:

```bash
streamlit run app.py --server.headless=true --browser.gatherUsageStats=false --server.fileWatcherType=none
```
