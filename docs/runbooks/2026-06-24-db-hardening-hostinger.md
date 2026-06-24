# Runbook — Database hardening on the Hostinger box (B5)

Defense-in-depth *under* the existing RLS. Goal: a compromised app process, a
leaked connection string, or a `pg_dump` can do as little as possible. Apply in
order; each step is reversible. **Do these in a no-exam window.**

## 1. Close the network surface (UFW) — do FIRST, verify SSH stays up

```bash
ufw status
ufw allow 22/tcp          # SSH — keep your session alive!
ufw allow 80/tcp
ufw allow 443/tcp
ufw default deny incoming
ufw default allow outgoing
ufw enable
# Postgres (5432) + PgBouncer must NOT be in the allow list → external-blocked.
ss -ltnp | grep -E ':5432|:6432' || echo "no public pg listeners"
```

## 2. Bind Postgres to localhost / unix socket only

In `postgresql.conf`:
```
listen_addresses = 'localhost'      # or '' for unix-socket-only
```
Confirm the app + PgBouncer connect via `127.0.0.1` or the unix socket, then
`systemctl restart postgresql`. Verify the app still works on a staging exam.

## 3. Least-privilege runtime role (the app must NOT be a superuser/owner)

The runtime role gets DML only — no DDL, no ownership. (RLS cutover to
`procta_app` is already underway — fold this in; see [[audit_rls_cutover_2026_06]].)

```sql
-- migration owner (used only by CI/migrations), and a locked-down runtime role:
-- runtime role: SELECT/INSERT/UPDATE/DELETE only, no DDL.
REVOKE CREATE ON SCHEMA public FROM PUBLIC;
ALTER DEFAULT PRIVILEGES IN SCHEMA public
  GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO procta_app;
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO procta_app;
GRANT USAGE ON SCHEMA public TO procta_app;
-- explicitly NOT granted: CREATE/ALTER/DROP, SUPERUSER, CREATEROLE, BYPASSRLS.
-- (RLS still applies to procta_app — it must NOT have BYPASSRLS.)

-- read-only analytics role (for any reporting feature; can never alter exam state):
CREATE ROLE procta_analyst NOLOGIN;
GRANT USAGE ON SCHEMA public TO procta_analyst;
GRANT SELECT ON ALL TABLES IN SCHEMA public TO procta_analyst;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT ON TABLES TO procta_analyst;
```
Point the app's connection string at `procta_app`. Keep the owner/migration role's
credentials OUT of the app env.

## 4. Tamper-proof audit log (grade-dispute defense)

Native logging of all data-modifying statements to a **root-owned** file the DB
user can't rewrite:
```
# postgresql.conf
log_statement = 'mod'                 # logs INSERT/UPDATE/DELETE/DDL
log_destination = 'stderr'
logging_collector = on
log_directory = '/var/log/postgresql' # ensure root-owned, postgres can only append
```
(Or `pgaudit` if available.) Confirm `/var/log/postgresql` is owned by root and the
postgres user can only append — so a DB compromise can't erase the trail of "who
changed a score and when."

## 5. Complements already in place / coming

- **RLS** — tenant isolation (existing).
- **Envelope encryption (B1)** — coding `expected_output` (and MCQ `correct` via
  `load_questions`) are AES-256-GCM encrypted with the key in the app env only, so
  a `pg_dump` can't read answer keys at rest. Run the backfill
  (`docs/coding-secrets-backfill.md`) after `CODING_SECRETS_KEY` is set.
- **TLS verify-full** — only relevant if DB traffic ever leaves localhost (e.g. a
  separate executor box). On the single-host setup, the unix socket / loopback
  bind makes this moot; revisit if the executor moves to its own VPS.

## Rollback
Each step is independent: `ufw disable`, revert `listen_addresses`, `GRANT`
back, or comment the logging lines + restart. Test each on staging first.
