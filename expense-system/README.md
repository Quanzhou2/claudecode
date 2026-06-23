# 🧾 Expense Reimbursement System

A self-hosted expense reimbursement web app with **LLM-powered receipt
recognition**, **duplicate-receipt blocking**, **role-based access** (user vs.
administrator), and an **intelligent natural-language query & analysis**
feature. Built with FastAPI + SQLite and a server-rendered UI — no build step,
runs from a single `uvicorn` process.

The LLM layer speaks the **OpenAI-compatible Chat Completions API**, so you can
point it at OpenAI, DeepSeek, Qwen/DashScope, Moonshot, Zhipu, a local Ollama /
vLLM server, or anything else that exposes that API — purely via configuration.
With no API key it runs in **offline mode**: receipts are entered manually and
analysis falls back to built-in aggregations.

---

## Features

| Area | What it does |
|------|--------------|
| 🔐 **Accounts & roles** | Register / login with signed-cookie sessions and bcrypt-hashed passwords. Two roles: **user** (create & view own records) and **admin** (view/edit *all* records, review, manage users). |
| 📷 **Receipt recognition** | Upload a receipt image → a vision LLM extracts vendor, date, amount, currency, category, tax and receipt number → you review & confirm before saving. |
| 🚫 **Duplicate detection** | Receipt numbers are normalized and enforced **globally unique** (DB constraint + friendly pre-check) so the same receipt can't be reimbursed twice — even by different users. |
| 🤖 **AI query & analysis** | Ask questions in plain language ("spend by category this quarter"). The LLM writes a read-only SQL query that runs against an **isolated, permission-scoped sandbox**, then summarizes the result. |
| ✅ **Approval workflow** | Records flow through `pending → approved / rejected / paid`. Admins review with an optional note; owners can edit only while `pending`. |
| 📊 **Dashboard** | Per-role summary cards plus spend-by-month and spend-by-category charts. |
| 🔎 **Search / filter / export** | Filter by status, category, date range and free text; paginate; export to CSV. |
| 🧾 **Audit log** | Every notable action (login, create, edit, review, role change) is recorded for traceability. |

---

## Language / 本地化

The UI ships in **Simplified Chinese (简体中文)**, and the LLM is prompted to
return Chinese categories and descriptions. Display strings are centralized to
keep re-localization easy:

- **Status / role / action / entity labels** — `app/models.py`
  (`STATUS_LABELS`, `ROLE_LABELS`, `ACTION_LABELS`, `ENTITY_LABELS`), surfaced
  via Jinja filters (`status_label`, `role_label`, …).
- **Categories** — `CATEGORIES` in `app/services/expenses.py`.
- **Page text** — the Jinja templates in `app/templates/`.
- **LLM prompts** — `app/llm/extraction.py` and `app/llm/analysis.py`.

Stored enum *values* (e.g. `pending`, `admin`) remain English for stability;
only their displayed labels are translated.

---

## Quickstart

```bash
cd expense-system
./run.sh
```

`run.sh` creates a virtualenv, installs dependencies, copies `.env.example` →
`.env`, seeds demo data, and starts the server at **http://localhost:8000**.

Or manually:

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --reload     # accounts are auto-created on first start
```

### Demo accounts

On first startup, when the database is empty, the app **auto-creates** these
accounts (set `AUTO_SEED=false` to disable):

| Role  | Username | Password   |
|-------|----------|------------|
| Admin | `admin`  | `admin123` |
| User  | `alice`  | `alice123` |

You can also (re)create them explicitly with `python -m scripts.seed`.

> **Change these before any real deployment.**
>
> **Can't log in?** It almost always means the server is reading a *different*
> SQLite file than the one that was seeded. The default DB path is now anchored
> to the project directory (`expense-system/expense.db`) so this can't happen
> from a stray working directory — but if you set a custom relative
> `DATABASE_URL`, make sure the seed step and the server use the same one. The
> seed script prints the exact DB path it wrote to.

---

## Configuration

All settings come from environment variables (or a `.env` file). See
[`.env.example`](.env.example).

| Variable | Default | Notes |
|----------|---------|-------|
| `SECRET_KEY` | `dev-secret-change-me` | Signs session cookies — **must** be changed in production. |
| `DATABASE_URL` | `sqlite:///<project>/expense.db` | Project-anchored absolute path by default. Any SQLAlchemy URL works. |
| `UPLOAD_DIR` | `<project>/uploads` | Where receipt images are stored. |
| `MAX_UPLOAD_MB` | `10` | Max image size. |
| `DEFAULT_CURRENCY` | `CNY` | Default for new records. |
| `AUTO_SEED` | `true` | Auto-create demo accounts when the DB is empty. |
| `LLM_API_KEY` | *(empty)* | Empty → **offline mode**. |
| `LLM_BASE_URL` | `https://api.openai.com/v1` | OpenAI-compatible endpoint. |
| `LLM_MODEL` | `gpt-4o-mini` | Text model (analysis). |
| `LLM_VISION_MODEL` | `gpt-4o` | Vision model (receipt OCR). |

### Example providers

| Provider | `LLM_BASE_URL` | text / vision models |
|----------|----------------|----------------------|
| OpenAI | `https://api.openai.com/v1` | `gpt-4o-mini` / `gpt-4o` |
| DeepSeek | `https://api.deepseek.com/v1` | `deepseek-chat` |
| Qwen (DashScope) | `https://dashscope.aliyuncs.com/compatible-mode/v1` | `qwen-plus` / `qwen-vl-plus` |
| Moonshot | `https://api.moonshot.cn/v1` | `moonshot-v1-8k` / `moonshot-v1-8k-vision-preview` |
| Ollama (local) | `http://localhost:11434/v1` | `llama3.1` / `llama3.2-vision` |

Receipt OCR requires the configured **vision** model to accept image input.

---

## How it works

### Duplicate detection
Receipt numbers are normalized (whitespace stripped, upper-cased) and stored
with a `UNIQUE` constraint. On create/edit the service checks for an existing
match and raises a friendly error; the DB constraint is the final safety net
against races. Records *without* a number are allowed (multiple are fine).

### AI analysis safety model
Natural-language questions never touch the live database directly. Instead:

1. The rows the current user is **allowed to see** (own records for users, all
   records for admins) are copied into a fresh **in-memory SQLite** database
   containing only an `expenses` table — no users, no password hashes, no other
   people's data.
2. The LLM writes a single SQL query, which is validated to be a **read-only
   `SELECT`/`WITH`** (no DML/DDL, no statement chaining) and run with
   `PRAGMA query_only = ON`.
3. The result is fed back to the LLM for a concise, factual summary.

Because the sandbox only ever contains permission-scoped rows, even a maliciously
generated query cannot read data the user shouldn't see.

---

## Testing

```bash
source .venv/bin/activate
pytest
```

Covers password hashing, auth/role gating, duplicate detection, cross-user
access control, receipt-extraction parsing (with a fake LLM), and the analysis
SQL-safety validator + sandbox isolation. The LLM is faked in tests, so no API
key or network is required.

---

## Docker

```bash
docker compose up --build
# then, once running, seed demo data:
docker compose exec app python -m scripts.seed
```

Data (SQLite DB + uploads) persists in the `expense_data` volume.

---

## Project structure

```
expense-system/
├── app/
│   ├── main.py            # FastAPI app, middleware, exception handlers
│   ├── config.py          # settings (env / .env)
│   ├── database.py        # engine, session, Base
│   ├── models.py          # User, Expense, AuditLog
│   ├── schemas.py         # ReceiptExtraction (Pydantic)
│   ├── security.py        # bcrypt hashing + sessions
│   ├── deps.py            # auth/role dependencies
│   ├── templating.py      # Jinja2 setup + filters
│   ├── llm/               # OpenAI-compatible client, extraction, analysis
│   ├── services/          # auth, expenses, audit business logic
│   ├── routers/           # auth, expenses, analytics, admin routes
│   ├── templates/         # Jinja2 HTML
│   └── static/            # CSS / JS
├── scripts/seed.py        # demo data
├── tests/                 # pytest suite
├── Dockerfile, docker-compose.yml, run.sh
└── requirements.txt
```

---

## Security notes & roadmap

This is a solid foundation intended for **authorized internal use**. Before a
production deployment consider:

- **CSRF protection** on state-changing POSTs (cookies are `SameSite=Lax` today).
- Serving over **HTTPS** and setting `Secure` cookies.
- Rate limiting on login and upload endpoints.
- Per-currency handling in analytics (amounts are not auto-converted).
- Cleanup of orphaned uploads when a review step is abandoned.
- Switching from SQLite to Postgres for concurrent multi-user load.
