# Deployment

Single-VM docker compose is the intended first deployment. **Honest caveat:
Docker was not running on the dev machine, so these steps are reviewed but not
executed; validate `docker compose config` on the target before relying on it.**

## Shape

    caddy (TLS, :443) -> api (uvicorn :8000) -> postgres, redis
                          worker (arq)      ->

## Steps

1. VM with Docker; clone the repo at a release tag.
2. `.env` from `.env.example`: set `ENVIRONMENT=production`, a long random
   `SECRET_KEY`, a real `POSTGRES_PASSWORD`, `PUBLIC_BASE_URL`, `EMAIL_BACKEND=smtp`
   + `SMTP_*`, `SENTRY_DSN`, `GOOGLE_API_KEY`, and `TRUSTED_PROXY_IPS` (the
   compose network address of caddy, e.g. the docker bridge subnet gateway).
3. TLS: run caddy (or traefik/nginx) on the host or as a compose service:

       your.domain {
           reverse_proxy 127.0.0.1:8000
       }

   Caddy provisions certificates automatically. HSTS is sent by the app when
   `ENVIRONMENT=production`.
4. `docker compose up -d --build` — the `migrate` service runs Alembic before
   api/worker start; a half-configured boot fails loudly by design (secret key,
   email backend, billing).
5. Backups: nightly `pg_dump` to off-VM storage, and **rehearse a restore
   once** before real users exist:

       docker compose exec db pg_dump -U katha kathasajha | gzip > backup.sql.gz

## Retention (backs the privacy page's claims)

The privacy policy states deleted data may persist in backups **up to 30 days**
and that IP-bearing security logs are kept **up to 30 days**. Both must actually
be enforced, or the page is an overclaim:

- Rotate backups on a 30-day cycle, e.g. `find /backups -name '*.sql.gz' -mtime +30 -delete`
  in the same cron entry that creates them.
- Container logs are capped by the `x-logging` block in docker-compose
  (20 MB x 5 files per service). If you ship logs elsewhere, set a 30-day
  retention there too.

## Scaling later

- More story throughput: `docker compose up -d --scale worker=3`
- More API throughput: raise uvicorn workers — but first raise Postgres
  `max_connections` or lower `DB_POOL_SIZE`/`DB_MAX_OVERFLOW`: 2 uvicorn
  workers + 1 arq worker ≈ 90 potential connections against a default of 100.
