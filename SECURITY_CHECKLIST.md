# FlowLeads CRM — Security Checklist

Use this checklist before production deployment and after major changes.

## Secrets and configuration

- [ ] No hardcoded secrets in source code or Docker images
- [ ] `.env` is listed in `.gitignore` and never committed
- [ ] `SECRET_KEY`, `DATABASE_URL`, `MAILGUN_API_KEY`, `OPENAI_API_KEY` set via environment only
- [ ] Production `FLASK_ENV=production` with secure session cookies

## Authentication and authorization

- [ ] All admin UI routes require login and role checks
- [ ] Superadmin routes require 2FA (`@require_2fa`)
- [ ] API routes require valid API key except `/api/v1/health` and Mailgun webhooks
- [ ] API keys stored as SHA-256 hashes only; full key shown once at creation
- [ ] `api_client` role cannot access the web UI

## Multi-tenancy

- [ ] All tenant data scoped by `organization_id`
- [ ] Superadmin cross-org access uses explicit `organization_id` parameter only

## Input and transport

- [ ] CSRF enabled on all form POST routes
- [ ] Input validation on forms and API payloads
- [ ] Rate limiting active (`LOGIN_RATE_LIMIT`, `API_RATE_LIMIT`; Redis in production)
- [ ] Mailgun webhook signatures verified when signing key is configured

## Audit and backups

- [ ] Audit log records critical actions (login, API keys, email, backups)
- [ ] Backup archives exclude secrets (passwords, TOTP, API key hashes, env secrets)
- [ ] Backup restore requires superadmin password + TOTP + explicit confirmation
- [ ] Backup filenames validated; path traversal rejected
- [ ] Retention cleanup deletes only `backup_YYYY_MM_DD_HHMMSS.tar.gz` files

## Operations

- [ ] Gunicorn serves the app (not Flask dev server) in production
- [ ] Scheduler runs in a separate process/container (`flask run-scheduler`), not in each Gunicorn worker
- [ ] TLS terminated at reverse proxy; security headers configured (see `deploy/nginx.conf`)
- [ ] Upload size limited (10MB at proxy; validate in app where applicable)

## Known MVP limitations

- [ ] AI enrichment queue is in-memory and not durable across restarts
- [ ] Documented in README for operators
