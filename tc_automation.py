"""
Trainer Central -> Dashboard automation.

One run does this:
  1. Wake up the dashboard API (GET /health).
  2. List the .txt files in the trainer's Google Drive folder.
  3. Skip the files already recorded in the local SQLite database.
  4. Ask Claude about each new file: "is this my submission email? what are the details?"
  5. POST my details to the dashboard (POST /api/entries).
  6. Record every handled file so it is never pushed twice.

All settings come from environment variables (or a local .env file). No secrets in this file.
"""
import json
import logging
import os
import re
import sqlite3
import sys
import time
from datetime import datetime, timedelta, timezone
from logging.handlers import RotatingFileHandler
from pathlib import Path

import requests
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")

# ---------- settings (all from environment) ----------
MY_NAME = os.getenv("MY_NAME", "").strip()
MY_EMAIL = os.getenv("MY_EMAIL", "").strip()
DRIVE_FOLDER_ID = os.getenv("DRIVE_FOLDER_ID", "").strip()
GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY", "").strip()
LOCAL_FOLDER = os.getenv("LOCAL_FOLDER", "").strip()          # optional: read files from a local folder instead of Drive
DASHBOARD_URL = os.getenv("DASHBOARD_URL", "https://trainer-central-assigments-api.onrender.com").rstrip("/")
DASHBOARD_API_KEY = os.getenv("DASHBOARD_API_KEY", "").strip()  # optional: only if the trainer says one is needed
CLAUDE_MODEL = os.getenv("CLAUDE_MODEL", "claude-haiku-5-5").strip()
DRY_RUN = os.getenv("DRY_RUN", "0").strip() == "1"            # 1 = do everything except the POST

DB_PATH = BASE_DIR / "processed.db"
LOG_PATH = BASE_DIR / "logs" / "automation.log"
PROMPT_PATH = BASE_DIR / "prompt.txt"
IST = timezone(timedelta(hours=5, minutes=30))

# ---------- logging ----------
LOG_PATH.parent.mkdir(exist_ok=True)
log = logging.getLogger("tc")
log.setLevel(logging.INFO)
_fmt = logging.Formatter("%(asctime)s | %(levelname)s | %(message)s")
for _h in (RotatingFileHandler(LOG_PATH, maxBytes=1_000_000, backupCount=3, encoding="utf-8"),
           logging.StreamHandler(sys.stdout)):
    _h.setFormatter(_fmt)
    log.addHandler(_h)


# ---------- small helper: HTTP call with retries ----------
def http(method, url, attempts=4, timeout=90, **kwargs):
    """Retry on network errors, timeouts, 429 and 5xx. Other answers are returned as they are."""
    waits = [5, 15, 30]
    last_error = None
    for i in range(attempts):
        try:
            r = requests.request(method, url, timeout=timeout, **kwargs)
            if r.status_code == 429 or r.status_code >= 500:
                last_error = f"HTTP {r.status_code}"
            else:
                return r
        except requests.RequestException as e:
            last_error = f"{type(e).__name__}: {e}"
        if i < attempts - 1:
            log.warning("%s %s failed (%s), retry %d/%d", method, url.split("?")[0], last_error, i + 1, attempts - 1)
            time.sleep(waits[min(i, len(waits) - 1)])
    raise RuntimeError(f"{method} {url.split('?')[0]} failed after {attempts} attempts: {last_error}")


# ---------- local record of processed files (R1, R5) ----------
def db_connect():
    con = sqlite3.connect(DB_PATH)
    con.execute("""CREATE TABLE IF NOT EXISTS processed (
        file_id TEXT PRIMARY KEY, file_name TEXT, modified TEXT, message_id TEXT,
        result TEXT, detail TEXT, processed_at TEXT)""")
    return con


def already_done(con, file_id, modified):
    row = con.execute("SELECT modified FROM processed WHERE file_id=?", (file_id,)).fetchone()
    return row is not None and row[0] == modified


def message_already_posted(con, message_id):
    if not message_id:
        return False
    return con.execute("SELECT 1 FROM processed WHERE message_id=? AND result='posted'", (message_id,)).fetchone() is not None


def record(con, f, result, message_id="", detail=""):
    con.execute("INSERT OR REPLACE INTO processed VALUES (?,?,?,?,?,?,?)",
                (f["id"], f["name"], f["modified"], message_id, result, detail[:500],
                 datetime.now(IST).isoformat(timespec="seconds")))
    con.commit()


# ---------- Google Drive (R1) ----------
def list_files():
    """Return [{'id','name','modified'}] for every .txt file, oldest first."""
    if LOCAL_FOLDER:
        files = [{"id": "local:" + p.name, "name": p.name, "modified": str(int(p.stat().st_mtime)), "path": p}
                 for p in Path(LOCAL_FOLDER).glob("*.txt")]
        return sorted(files, key=lambda f: f["modified"])
    files, token = [], None
    while True:
        params = {"q": f"'{DRIVE_FOLDER_ID}' in parents and trashed = false",
                  "fields": "nextPageToken, files(id, name, modifiedTime)",
                  "pageSize": 1000, "orderBy": "modifiedTime", "key": GOOGLE_API_KEY,
                  "supportsAllDrives": "true", "includeItemsFromAllDrives": "true"}
        if token:
            params["pageToken"] = token
        r = http("GET", "https://www.googleapis.com/drive/v3/files", params=params, timeout=60)
        if r.status_code != 200:
            raise RuntimeError(f"Drive list failed: HTTP {r.status_code} {r.text[:300]}")
        data = r.json()
        files += [{"id": x["id"], "name": x["name"], "modified": x.get("modifiedTime", "")}
                  for x in data.get("files", []) if x["name"].lower().endswith(".txt")]
        token = data.get("nextPageToken")
        if not token:
            return files


def read_file(f):
    if "path" in f:
        return f["path"].read_text(encoding="utf-8", errors="replace")
    r = http("GET", f"https://www.googleapis.com/drive/v3/files/{f['id']}",
             params={"alt": "media", "key": GOOGLE_API_KEY, "supportsAllDrives": "true"}, timeout=60)
    if r.status_code != 200:
        raise RuntimeError(f"Drive download failed: HTTP {r.status_code} {r.text[:200]}")
    r.encoding = "utf-8"
    return r.text


# ---------- Claude analysis (R2, R3) ----------
_client = None


def analyse_with_claude(file_name, text):
    """Send one file to Claude, get back a dict (see prompt.txt for the fields)."""
    global _client
    if _client is None:
        import anthropic
        _client = anthropic.Anthropic(max_retries=3, timeout=60)   # key is read from ANTHROPIC_API_KEY
    system = PROMPT_PATH.read_text(encoding="utf-8").replace("{MY_NAME}", MY_NAME).replace("{MY_EMAIL}", MY_EMAIL)
    user = f"File name: {file_name}\n\n<email_file>\n{text[:20000]}\n</email_file>"
    msg = _client.messages.create(model=CLAUDE_MODEL, max_tokens=2000, system=system,
                                  messages=[{"role": "user", "content": user}])
    reply = "".join(b.text for b in msg.content if getattr(b, "type", "") == "text")
    m = re.search(r"\{.*\}", reply, re.DOTALL)
    if not m:
        raise ValueError(f"Claude did not return JSON: {reply[:200]}")
    return json.loads(m.group(0))


def build_entry_text(d, file_name):
    """Turn Claude's JSON into the 'text' field for the dashboard (1 to 10,000 characters)."""
    when = d.get("submitted_at_utc") or "unknown"
    try:
        dt = datetime.fromisoformat(when.replace("Z", "+00:00"))
        when = f"{dt.astimezone(IST):%d %b %Y, %I:%M %p} IST ({when})"
    except ValueError:
        pass
    parts = [("Assignment", d.get("assignment_name")), ("Course", d.get("course")),
             ("Submitted", when), ("Status", d.get("status")),
             ("Learner", d.get("learner_email")), ("Submission ID", d.get("submission_id")),
             ("Message ID", d.get("message_id")), ("Link", d.get("submission_link")),
             ("Source file", file_name)]
    return " | ".join(f"{k}: {v}" for k, v in parts if v)[:10000]


# ---------- dashboard API (R4) ----------
def api_headers():
    h = {"Content-Type": "application/json"}
    if DASHBOARD_API_KEY:
        h["x-api-key"] = DASHBOARD_API_KEY
    return h


def wake_api():
    r = http("GET", f"{DASHBOARD_URL}/health", timeout=90)
    log.info("API health: HTTP %s", r.status_code)


def already_on_dashboard(message_id):
    """Second safety net against duplicates: is this Message ID already in GET /api/entries?"""
    if not message_id:
        return False
    try:
        r = http("GET", f"{DASHBOARD_URL}/api/entries", headers=api_headers(), attempts=2)
        return r.status_code == 200 and f"Message ID: {message_id}" in r.text
    except Exception as e:
        log.warning("Could not check existing entries (%s); relying on the local record", e)
        return False


def post_entry(text):
    r = http("POST", f"{DASHBOARD_URL}/api/entries", headers=api_headers(), json={"text": text, "sender": MY_NAME})
    if r.status_code not in (200, 201):
        raise RuntimeError(f"POST /api/entries rejected: HTTP {r.status_code} {r.text[:300]}")
    return r.status_code, r.text[:300]


# ---------- one run ----------
def check_settings():
    missing = [n for n, v in [("MY_NAME", MY_NAME), ("MY_EMAIL", MY_EMAIL),
                              ("ANTHROPIC_API_KEY", os.getenv("ANTHROPIC_API_KEY", ""))] if not v]
    if not LOCAL_FOLDER:
        missing += [n for n, v in [("DRIVE_FOLDER_ID", DRIVE_FOLDER_ID), ("GOOGLE_API_KEY", GOOGLE_API_KEY)] if not v]
    if not 2 <= len(MY_NAME) <= 100:
        missing.append("MY_NAME (must be 2 to 100 characters)")
    return missing


def main():
    log.info("===== run started%s =====", " (DRY RUN)" if DRY_RUN else "")
    missing = check_settings()
    if missing:
        log.error("Missing settings: %s. Fill them in the .env file.", ", ".join(dict.fromkeys(missing)))
        return 1
    stats = dict(seen=0, new=0, mine=0, posted=0, duplicate=0, not_mine=0, empty=0, errors=0)
    con = db_connect()

    try:
        files = list_files()
    except Exception as e:
        log.error("Could not read the Drive folder, will try again next run: %s", e)
        return 1
    stats["seen"] = len(files)
    todo = [f for f in files if not already_done(con, f["id"], f["modified"])]
    stats["new"] = len(todo)
    log.info("Files in folder: %d | new since last run: %d", len(files), len(todo))

    api_awake = False
    for f in todo:
        try:   # one bad file must never stop the run
            text = read_file(f)
            if not text.strip():
                stats["empty"] += 1
                record(con, f, "empty")
                log.info("SKIP empty file: %s", f["name"])
                continue

            d = analyse_with_claude(f["name"], text)
            mine = bool(d.get("is_submission_email")) and bool(d.get("is_mine"))
            # sanity guard: my email or name must really be in the file
            if mine and MY_EMAIL.lower() not in text.lower() and MY_NAME.lower() not in text.lower():
                log.warning("Claude said 'mine' but my name/email is not in %s; treating as not mine", f["name"])
                mine = False
            if not mine:
                stats["not_mine"] += 1
                record(con, f, "not_mine", detail=str(d.get("reason", "")))
                log.info("NOT MINE: %s (%s)", f["name"], d.get("reason", ""))
                continue

            stats["mine"] += 1
            mid = str(d.get("message_id") or "")
            entry_text = build_entry_text(d, f["name"])
            log.info("MINE: %s -> %s", f["name"], entry_text)

            if DRY_RUN:
                log.info("DRY RUN: not posting, not recording")
                continue
            if not api_awake:
                wake_api()
                api_awake = True
            if message_already_posted(con, mid) or already_on_dashboard(mid):
                stats["duplicate"] += 1
                record(con, f, "duplicate", mid)
                log.info("DUPLICATE, not posting again: %s", f["name"])
                continue

            status, body = post_entry(entry_text)
            stats["posted"] += 1
            record(con, f, "posted", mid, f"HTTP {status} {body}")
            log.info("POSTED: %s | HTTP %s | %s", f["name"], status, body)
        except Exception as e:
            stats["errors"] += 1   # not recorded, so the next run tries this file again
            log.error("ERROR on %s (will retry next run): %s: %s", f["name"], type(e).__name__, e)

    log.info("===== run finished | %s =====", " | ".join(f"{k}={v}" for k, v in stats.items()))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as e:   # last line of defence: log, never crash silently
        log.exception("Run crashed: %s", e)
        sys.exit(1)
