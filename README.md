# Trainer Central to Dashboard automation

A small Python program that runs on my own Windows computer every 30 minutes. It reads the Trainer Central submission emails saved in the trainer's Google Drive folder, uses Claude to find the ones that belong to me and extract the assignment details, and posts those details to the trainer's dashboard API. After setup there are no manual steps.

## Architecture

```
Windows Task Scheduler (every 30 minutes)
        |
        v
   run.bat  ->  tc_automation.py
                    |
                    | 1. list .txt files            +----------------------------+
                    +------------------------------>| Google Drive shared folder |
                    |    (Drive API, read only)     +----------------------------+
                    |
                    | 2. skip files already in      +----------------------------+
                    +------------------------------>| processed.db (local SQLite)|
                    |                               +----------------------------+
                    |
                    | 3. "is this mine? details?"   +----------------------------+
                    +------------------------------>| Claude (Anthropic API)     |
                    |    JSON answer (prompt.txt)   +----------------------------+
                    |
                    | 4. POST /api/entries          +----------------------------+
                    +------------------------------>| Trainer dashboard API      |
                    |    {text, sender}             +----------------------------+
                    |
                    | 5. record the file, write the log
                    v
              processed.db, logs/automation.log
```

## Files

| File | Purpose |
|---|---|
| `tc_automation.py` | The application |
| `prompt.txt` | The Claude analysis prompt (returns strict JSON) |
| `.env.example` | Template for settings; the real `.env` holds the keys and is not submitted |
| `requirements.txt` | Python packages |
| `run.bat` | Runs one pass |
| `schedule_task.bat` | Creates the Windows scheduled task (every 30 minutes) |

## Setup

1. Install Python 3 (tick "Add python.exe to PATH").
2. In the project folder:
   ```
   python -m venv .venv
   .venv\Scripts\pip install -r requirements.txt
   ```
3. Copy `.env.example` to `.env` and fill in `MY_NAME`, `MY_EMAIL`, `ANTHROPIC_API_KEY` and `GOOGLE_API_KEY`.
   The Google API key comes from Google Cloud Console (a project with the Google Drive API enabled, key restricted to the Drive API).

## How to run

- Once, by hand: `.venv\Scripts\python tc_automation.py`
- Test without posting: set `DRY_RUN=1` in `.env`.
- Test with local files instead of Drive: set `LOCAL_FOLDER` in `.env` to a folder of `.txt` files.

## How it is scheduled

Double-click `schedule_task.bat` once. It creates a Windows Task Scheduler task named `TrainerCentralAutomation` that runs `run.bat` every 30 minutes, so a new submission reaches the dashboard well inside the 1-hour limit.

- Check it: `schtasks /Query /TN "TrainerCentralAutomation"`
- Remove it: `schtasks /Delete /TN "TrainerCentralAutomation" /F`

## How each requirement is met

| # | Requirement | How |
|---|---|---|
| R1 | Fetch from Drive, new files only | Drive API lists the folder; file IDs already in `processed.db` are skipped |
| R2 | Identify my emails | Claude reads each file and returns `is_mine` (matching on my name and email) |
| R3 | Extract details | Claude returns assignment name, course, submission time, status, submission ID and link as JSON |
| R4 | Push to dashboard | `POST /api/entries` with `text` and `sender`; the response status is checked |
| R5 | No duplicates | Local record by Drive file ID and by email Message ID, plus a check of `GET /api/entries` before posting |
| R6 | Fully automated | Windows Task Scheduler, every 30 minutes |
| R7 | Runs locally | Everything runs on my own computer |
| R8 | Resilient | Retries with waits for API and Drive calls, `GET /health` first to wake the server, one bad file never stops the run, every run is logged to `logs/automation.log` |

## Assumptions

- The Drive folder is shared as "anyone with the link", so a Google API key is enough to read it.
- The emails name the learner by email address, so the match depends mainly on `MY_EMAIL`.
- The email does not state a status, so an "Assignment submitted" email is recorded as status `submitted`.
- Submission time is the email's Date header (UTC), shown in IST as well.
- The dashboard accepted posts without an `x-api-key`; the program sends one only if `DASHBOARD_API_KEY` is set.
- The computer must be switched on, awake and connected to the internet for scheduled runs to happen.
- A file that fails (Claude or API error) is not recorded, so the next run tries it again.
