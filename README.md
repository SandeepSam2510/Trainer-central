# Trainer Central – Assignment Dashboard Chatbot

A small web chatbot that answers questions about the trainer's assignment dashboard, for example:

- "How many times did Rohith submit the assignment?"
- "Who submitted the Customer Support Ticket Triage assignment?"
- "List all of Sandeep's entries with their times in IST."

It also contains my Trainer Central → Dashboard automation assignment in the [`automation/`](automation/) folder.

## How it works

```
Browser (static/index.html)
   |  POST /api/chat  {messages: [...]}
   v
FastAPI backend (app.py, hosted on Render)
   |-- GET /api/entries  -->  Trainer dashboard API (trainer-central-assigments-api.onrender.com)
   |-- messages + entries --> Claude (Anthropic API)
   v
{reply: "..."}  -> shown in the chat window
```

- The dashboard entries are fetched on every question (cached for 60 seconds), so answers are always up to date.
- The Anthropic API key lives only in Render's environment variables, never in the code or in the browser.

## Files

| File | Purpose |
|---|---|
| `app.py` | FastAPI backend: `/` chat page, `/api/chat`, `/health` |
| `static/index.html` | The chat page (HTML, CSS, JavaScript) |
| `requirements.txt` | Python packages |
| `render.yaml` | Render Blueprint (build/start commands, env vars) |
| `.env.example` | Settings template for running on your own computer |
| `automation/` | The Trainer Central → Dashboard automation assignment |

## Settings (environment variables)

| Name | Required | Value |
|---|---|---|
| `ANTHROPIC_API_KEY` | yes | Your Anthropic API key |
| `CLAUDE_MODEL` | no | default `claude-haiku-5-5` |
| `DASHBOARD_URL` | no | default `https://trainer-central-assigments-api.onrender.com` |
| `DASHBOARD_API_KEY` | no | only if the dashboard API needs an `x-api-key` |

## Run on your own computer

```
python -m venv .venv
.venv\Scripts\activate          (Mac/Linux: source .venv/bin/activate)
pip install -r requirements.txt
copy .env.example .env          (Mac/Linux: cp .env.example .env)  then put your key in .env
uvicorn app:app --reload
```

Open http://127.0.0.1:8000

## Deploy on Render

1. Sign in at https://render.com with GitHub.
2. **New + → Web Service** → pick this repository.
3. Settings:
   - Runtime: **Python 3**
   - Build Command: `pip install -r requirements.txt`
   - Start Command: `uvicorn app:app --host 0.0.0.0 --port $PORT`
   - Instance type: **Free**
4. **Environment Variables**: add `ANTHROPIC_API_KEY` (and optionally `CLAUDE_MODEL`, `DASHBOARD_URL`).
5. **Deploy Web Service**. The site is live at `https://<service-name>.onrender.com`.

On the free plan the service sleeps after 15 minutes without visitors; the first question after that takes about a minute.
