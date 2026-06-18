# Archive

Historical planning, audit, and review documents. Preserved for reference but
no longer canonical. Current strategic guidance lives in
[`docs/STRATEGIC_AUDIT_2026-06-14.md`](../STRATEGIC_AUDIT_2026-06-14.md); the
current gap audit is [`docs/GAP_AUDIT_2026-06-14.md`](../GAP_AUDIT_2026-06-14.md).

| File | Original date | Replaced by / why archived |
|---|---|---|
| `PROJECT_CTO_AUDIT.md` | 2026-05-24 | Superseded by `docs/STRATEGIC_AUDIT_2026-06-14.md`. |
| `DB_INDEX_REVIEW.md` | 2026-05-15 | One-off DB index review. Findings either applied or deferred to backlog. |
| `DEPLOY_INVITES.md` | 2026-05-06 | Old phase-specific deploy notes (invite rollout). Superseded by ops in `DEPLOY.md`. |
| `PLAN.md` | 2026-05-14 | Historical sprint plan. New planning lives in per-task plan files. |
| `QUALITY_REVIEW.md` | 2026-05-15 | One-off review. |
| `USER_WORKFLOW_AUDIT.md` | 2026-05-17 | 91 KB legacy audit. Items either resolved or rolled into the strategic audit. |
| `selfhosting-analysis.md` | 2026-05-23 | One-off feasibility analysis. |
| `Procta_Performance.pdf` | 2026-05-23 | Old performance report. No regen script — kept as-is for historical reference. |
| `STRATEGIC_AUDIT_2026-05-27.md` | 2026-05-27 | Superseded by `docs/STRATEGIC_AUDIT_2026-06-14.md`. |
| `GAP_ANALYSIS_2026-06-12.md` | 2026-06-12 | Superseded by `docs/GAP_AUDIT_2026-06-14.md`. |
| `HANDOFF.md` | 2026-05-28 | Point-in-time session snapshot; pointed at the superseded May-27 audit. |
| `HANDOFF_2026-06-14.md` | 2026-06-14 | Ephemeral session handoff; no longer current. |
| `QA_CHECKLIST_2026-06-10.md` | 2026-06-10 | Manual QA checklist for an uncommitted batch that has long since shipped. |
| `POSTGRES_AUTH_MIGRATION_PLAN.md` | 2026-05-19 | Migration complete — `app/database.py` now runs plain Postgres only (Supabase backend unsupported); local auth lives in `app/services/local_auth.py`. |
| `TENANCY_HARDENING_RUNBOOK.md` | 2026-06-01 | phase79/phase80 FK-constraint work shipped (now at phase129); its "future work" (app-level RLS) is implemented and documented in `docs/TENANCY_RLS_HARDENING.md`. |

If something here turns out to be needed at the repo root again,
`git mv docs/archive/<file> <file>` brings it back with full history intact.
