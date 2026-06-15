# React-only → Legacy dashboard port plan (IN PROGRESS)

**Status (2026-06-16):** NOT complete. The React footprint
(`app/dashboard-ui/`, `app/student-ui/`, `app/static/*-react/`, the
`/dashboard-react` + `/student-react` routes, the Dockerfile `uibuilder` stage,
the Caddy routes, and CI builds) is **still in place and must stay** until the
features below are ported. ~7 features still live ONLY in the React source (their
backends exist; legacy HTML has no UI yet). **Decision (2026-06-16): port each
feature to legacy HTML first, THEN delete React** — do not delete the source
while it's the only reference for an un-ported feature.

Removal happens ONLY after the checklist below is all ✅; the final deletion PR
then strips: `public.py` routes, `app/dashboard-ui/`+`app/student-ui/`,
`app/static/*-react/`, Dockerfile `uibuilder`, Caddy `*-react` routes,
`main.py` `/student-react` carve-out, `test.yml` React builds,
`tests/browser/...:/dashboard-react`, and the dependabot/codeql/quality_check refs.

## Legacy wiring recipe (CSP-safe — follow exactly)
The dashboard CSP is `script-src 'self'` (NO inline `<script>`/`onclick`). So:
1. Add a tab button in `app/static/dashboard.html` (~lines 341–360) next to siblings:
   `<button class="tab" data-tab="<name>" role="tab" data-action="switchTab"
   data-args='["<name>"]' data-roles="admin superadmin" style="display:none">Label</button>`
   (use `data-roles="superadmin"` for superadmin-only).
2. Add the panel `<div id="tab-<name>" ...>` in the panels area.
3. Wire load in `dashboard-app.js` `switchTab()` (see `if(tab==='all-orgs') loadAllOrgs()`).
4. Render via a `load<Name>()` fn using `authFetch(`${BASE}<endpoint>`)`; buttons use
   `data-action="..."` delegated handlers (grep `data-action` for the pattern). Never inline JS.
5. Reuse existing helpers: `authFetch`, `_escHtml`, `showModal`, `appConfirm`, `_getReauthToken`.
Each feature = one additive PR → CI (`pytest`/`integration`/`e2e-electron`/`docker-smoke`) gates it.

## Features to port (priority order)

### 1. Billing: usage + cancel  🔴
- Endpoints: `GET /api/v1/billing/usage` (students_used, plan_limit, overage, overage_charges), `POST /api/v1/billing/cancel`.
- React src: `app/dashboard-ui/src/panels/BillingPanel.jsx`.
- Legacy: billing tab exists (`loadBilling`, upgrade/change/portal at dashboard-app.js ~3622–3740). ADD: a usage block (call /usage, render students_used vs plan_limit + overage_charges table) and a "Cancel plan" button → POST /cancel (confirm dialog; admin/superadmin only). No new tab needed — extend the billing panel.

### 2. Cluster & Batch Review  🔴
- Endpoints: `GET /api/v1/admin/violations/clusters` (+ optional query), `POST /api/v1/admin/violations/bulk-dismiss`.
- React src: `app/dashboard-ui/src/panels/ReviewPanel.jsx`.
- Legacy: NEW tab `review` (admin+superadmin). Render clusters grouped by type/severity with counts; checkbox-select + "Bulk dismiss" → POST bulk-dismiss with the selected ids; refresh on success.

### 3. Bulk student import  🔴
- Endpoints: `POST /api/v1/admin/register-students-bulk` (JSON list) and/or `POST /api/v1/admin/students/import-csv` (file). Confirm body shape from `app/routers/admin_students.py:703` (UploadFile/Form).
- React src: `app/dashboard-ui/src/panels/BulkImportPanel.jsx`.
- Legacy: extend the Members/Students area (or a new `import` section). File input (`data-change-action`, see `#invite-csv` pattern) → POST; show per-row results.

### 4. Per-exam audio-keyword config  🟠
- Endpoint: `/api/v1/admin/audio-keywords` (GET/POST).
- React src: search `app/dashboard-ui/src` for `audio-keywords`.
- Legacy: add to the exam-config / Questions area — list keywords + add/remove → POST.

### 5. Proctoring-sensitivity config  🟠
- Endpoint: `/api/v1/admin/proctoring-sensitivity` (GET/POST).
- Legacy: add a select (lenient/balanced/strict) to exam settings → POST.

### 6. Teacher data-subject (DPDP SAR) panel  🟠
- Endpoints: `/api/v1/privacy/export`, `/api/v1/privacy/delete` (POST). Note: these route to admin_sar (`_require_superadmin`, env-pinned) — confirm RBAC before exposing; may be superadmin-only.
- React src: `app/dashboard-ui/src/panels/PrivacyPanel.jsx`.
- Legacy: NEW tab `privacy` (gate to the role the endpoint actually allows).

### 7. live-monitor  🟡
- Endpoint: `/api/v1/admin/live-monitor`. Legacy already has Live Sessions via SSE — first check what this adds; may be redundant (skip if so).

### 8. access-code/clear  🟡
- Endpoint: `/api/v1/admin/access-code/clear`. Small admin action; add a button to the exam/access-code area.

## Post-deletion notes
The following inactive reference files remain (they're exclusion/ignore patterns
for paths that no longer exist — harmless):
- `.pre-commit-config.yaml` — `dashboard-react/assets` exclusion
- `.github/codeql/codeql-config.yml` — exclusion paths for deleted dirs
- `.codecov.yml` — ignore flags for deleted dirs
- `.coderabbit.yaml` — ignore patterns for deleted dirs
- Do NOT touch `website/` (live marketing site) or the legacy
`dashboard.html`/`dashboard-app.js`/`student.html`/`student-app.js`.
