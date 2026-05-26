# FlowLeads CRM

FlowLeads is a multi-tenant Flask CRM for lead management, pipeline tracking, REST API integrations (n8n), AI enrichment, email (Mailgun), analytics, and production backups.

## Stack

- Python 3.11+ / Flask
- PostgreSQL + SQLAlchemy + Alembic
- Flask-Login, Flask-WTF (CSRF), Flask-Limiter, Flask-Caching
- Gunicorn + Nginx (production)
- Docker + docker-compose (app, db, redis, scheduler)
- APScheduler (scheduled backups)
- Redis (optional: rate limit storage + cache in production)

## Quick start (Docker)

```bash
cp .env.example .env          # Windows: copy .env.example .env
# Edit .env: set SECRET_KEY (required) and DATABASE_URL if not using the default
docker compose up --build
```

`app` and `scheduler` load variables from `.env` via `env_file` and from `environment` in `docker-compose.yml` (with defaults for Docker networking paths). Do not commit `.env`.

App listens on **http://localhost:8000**. Scheduler runs daily backups at **02:00 UTC**.

## Development setup

```bash
python -m venv .venv
.\.venv\Scripts\activate          # Windows
source .venv/bin/activate         # Linux/macOS
pip install -r requirements.txt
cp .env.example .env
flask db upgrade
flask create-superadmin
flask run
```

## Environment variables

| Variable | Description |
|----------|-------------|
| `FLASK_ENV` | `development`, `production`, or `testing` |
| `SECRET_KEY` | Flask session signing key (required) |
| `DATABASE_URL` | PostgreSQL connection URL (required in prod) |
| `MAILGUN_API_KEY` | Mailgun API key for sending |
| `MAILGUN_DOMAIN` | Mailgun domain |
| `MAILGUN_FROM_EMAIL` | Default From address |
| `MAILGUN_FROM_NAME` | Default From display name |
| `MAILGUN_WEBHOOK_SIGNING_KEY` | Webhook signature verification |
| `EMAIL_SENDING_ENABLED` | `true` / `false` |
| `BACKUP_DIR` | Directory for `.tar.gz` backups (default `./backups`) |
| `BACKUP_RETENTION_DAYS` | Delete backups older than N days (default 30) |
| `UPLOAD_DIR` | User uploads directory (default `./uploads`) |
| `REDIS_URL` | Redis URL for production cache/rate limits |
| `LOGIN_RATE_LIMIT` | e.g. `5/minute` |
| `API_RATE_LIMIT` | e.g. `100/hour` |
| `OPENAI_API_KEY` | OpenAI for lead enrichment |
| `AI_ENRICHMENT_ENABLED` | Enable/disable AI features |
| `AI_ENRICHMENT_MODEL` | Model name (default `gpt-4o-mini`) |
| `PUBLIC_REGISTRATION_ENABLED` | Allow public signup (default `false`) |

Never commit `.env` to version control.

## Database migrations

```bash
flask db upgrade
```

## Creating superadmin

```bash
flask create-superadmin
# or: flask create-superadmin --email admin@example.com --password '...'
```

Complete **2FA setup on first login**. Superadmin routes require verified 2FA session.

## CLI commands

| Command | Description |
|---------|-------------|
| `flask create-superadmin` | Create superadmin user |
| `flask db upgrade` | Apply migrations |
| `flask backup-create` | Create backup now |
| `flask backup-restore <filename>` | Restore (password + 2FA prompts) |
| `flask rotate-api-key <key-id>` | Revoke key and issue new one (prints new key once) |
| `flask send-test-email <email>` | Send Mailgun test message |
| `flask run-scheduler` | Run APScheduler (backups at 02:00 UTC) |

## Backup and restore

**Create:** Admin → Backups (`/admin/backups`) or `flask backup-create`.

Backups include: PostgreSQL dump, email templates JSON, system settings JSON (no secrets), uploads folder, manifest.

**Restore:** Requires superadmin, password, TOTP, and confirmation checkbox. **Overwrites current data.** A safety backup is attempted first.

```bash
flask backup-restore backup_2025_05_26_020000.tar.gz
```

## n8n integration

1. Create an API key in **Settings → API keys** (org admin) or **Admin → API keys** (superadmin).
2. Copy the key immediately — it is **shown only once**.
3. In n8n, add an HTTP Request node:

- **Method:** POST  
- **URL:** `https://your-domain.com/api/v1/leads`  
- **Authentication:** Header Auth  
- **Header:** `Authorization: Bearer YOUR_API_KEY`  
- **Body (JSON):**

```json
{
  "email": "john@example.com",
  "first_name": "John",
  "last_name": "Doe",
  "company": "Acme Corp",
  "source": "n8n",
  "source_ref": "{{$json.id}}",
  "tags": ["n8n", "automated"]
}
```

Bulk import: `POST /api/v1/leads/bulk` (max 100 leads per request).

## API reference

**Authentication:** `Authorization: Bearer <api_key>` or `X-API-Key: <api_key>`

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/v1/health` | Health check (no auth) |
| GET | `/api/v1/me` | API key metadata |
| POST | `/api/v1/leads` | Create or upsert lead |
| POST | `/api/v1/leads/bulk` | Bulk create/update |
| GET | `/api/v1/leads` | List leads |
| GET | `/api/v1/leads/<id>` | Lead detail |
| PATCH | `/api/v1/leads/<id>` | Update lead |
| GET | `/api/v1/pipeline/stages` | Pipeline stages |
| POST | `/api/v1/leads/<id>/enrich` | Queue AI enrichment |

## Email template variables

Allowed placeholders: `{{ first_name }}`, `{{ last_name }}`, `{{ company }}`, `{{ sender_name }}`, `{{ ai_summary }}`.

## Tests and coverage

```bash
set FLASK_ENV=testing
pytest
pytest --cov=app --cov-report=term-missing
```

Target: **80%+** coverage (`/.coveragerc`).

## Deploy on Render

Production on [Render](https://render.com) uses **Gunicorn** for the web app and a **separate background worker** for APScheduler (scheduled backups). Docker Compose remains for local development only.

You can deploy manually (steps below) or connect the GitHub repo and use the included [`render.yaml`](render.yaml) blueprint.

### 1. Create PostgreSQL

In the Render dashboard: **New → PostgreSQL**. Note the internal **Database URL** (or link it to services below so `DATABASE_URL` is injected automatically).

### 2. Create Web Service

**New → Web Service** → connect `flowlyconnect-debug/flowleads-crm`.

| Setting | Value |
|---------|--------|
| **Runtime** | Python 3 |
| **Build Command** | `pip install -r requirements.txt` |
| **Start Command** | `gunicorn run:app --workers 4 --bind 0.0.0.0:$PORT` |
| **Release Command** (recommended) | `flask db upgrade` |

Render sets **`PORT`** automatically; Gunicorn must bind to `0.0.0.0:$PORT`.

Link the PostgreSQL instance so **`DATABASE_URL`** is available to the service.

### 3. Create Background Worker

**New → Background Worker** → same repo.

| Setting | Value |
|---------|--------|
| **Build Command** | `pip install -r requirements.txt` |
| **Start Command** | `flask run-scheduler` |

Use the **same** `SECRET_KEY`, `DATABASE_URL`, `FLASK_APP`, and `FLASK_ENV` as the web service. APScheduler runs only in this process—not inside Gunicorn workers.

### 4. Required environment variables (Web + Worker)

| Variable | Required | Notes |
|----------|----------|--------|
| `FLASK_APP` | Yes | `run.py` |
| `FLASK_ENV` | Yes | `production` (disables Flask debug) |
| `SECRET_KEY` | Yes | Long random string; never commit |
| `DATABASE_URL` | Yes | From Render PostgreSQL (linked) |
| `MAILGUN_API_KEY` | For email | Mailgun sending |
| `MAILGUN_DOMAIN` | For email | |
| `MAILGUN_FROM_EMAIL` | For email | |
| `MAILGUN_FROM_NAME` | Optional | Default `FlowLeads` |
| `MAILGUN_WEBHOOK_SIGNING_KEY` | For webhooks | |
| `OPENAI_API_KEY` | For AI | Lead enrichment |
| `REDIS_URL` | Optional | Rate limits + cache (Render Key Value or external Redis) |
| `BACKUP_DIR` | Optional | Default `./backups` |
| `UPLOAD_DIR` | Optional | Default `./uploads` |
| `EMAIL_SENDING_ENABLED` | Optional | `true` / `false` |
| `PUBLIC_REGISTRATION_ENABLED` | Optional | Default `false` |

After the first deploy, open the web service shell (or one-off job) and run:

```bash
flask create-superadmin
```

### 5. Post-deploy checks

- `GET /api/v1/health` returns OK.
- Web logs show Gunicorn workers, not `flask run`.
- Scheduler worker logs show “Starting backup scheduler (daily 02:00 UTC)”.
- `FLASK_ENV=production` — no Flask debug mode.

**Note:** Render’s filesystem is ephemeral unless you attach a **persistent disk** for `BACKUP_DIR` and `UPLOAD_DIR`. For production backups/uploads, add a disk or use external storage.

## Production deployment checklist

- [ ] Set strong `SECRET_KEY` and production `DATABASE_URL`
- [ ] `FLASK_ENV=production`
- [ ] Run `flask db upgrade` on deploy
- [ ] Use Gunicorn (Dockerfile) — not `flask run`
- [ ] Run scheduler as separate service: `flask run-scheduler`
- [ ] Configure Redis (`REDIS_URL`) for rate limits/cache
- [ ] Mount `backup_data` and `uploads_data` volumes
- [ ] Install `postgresql-client` on backup host (included in Docker image)
- [ ] Place Nginx in front (`deploy/nginx.conf` example)
- [ ] Enable TLS and security headers
- [ ] Review `SECURITY_CHECKLIST.md`

## Security notes

- API keys are SHA-256 hashed; full key shown **only once** at creation.
- Backup exports exclude passwords, TOTP secrets, and API key hashes.
- Restore requires superadmin **password + 2FA**.
- AI enrichment uses an **in-memory queue** (MVP) — jobs are lost on restart.
- Mailgun webhooks should use signature verification in production.

See [SECURITY_CHECKLIST.md](SECURITY_CHECKLIST.md) for the full list.
