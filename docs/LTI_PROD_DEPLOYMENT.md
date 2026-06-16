# LMS Integration — Production Deployment Runbook

How to take Procta's LMS integrations live on prod. Three independent
integrations, deployed independently:

| Integration | Protocol | Endpoints | Env to set |
|---|---|---|---|
| Canvas | LTI 1.3 | `/lti/*` | `LTI_PRIVATE_KEY`, `LTI_REGISTRATIONS` |
| Moodle | LTI 1.3 | `/lti/*` | `LTI_PRIVATE_KEY`, `LTI_REGISTRATIONS` |
| Google Classroom | OAuth2 + Classroom API | `/api/v1/google/*` | `GOOGLE_CLASSROOM_CLIENT_ID/SECRET` |

Prod loads env from `/root/proctor-browser/.env`; reload with
`docker compose up -d api`. All `/lti/*` and `/api/v1/google/*` endpoints
are already deployed — these integrations are **dormant** only because the
env above is unset. Both fail **closed** (LTI launches 401/403, the Google
"Connect" button stays hidden) until configured, so there is no risk in
leaving them off.

---

## Phase 1 — Persistent LTI signing key (REQUIRED for Canvas + Moodle)

Today prod serves an **ephemeral** LTI key (`kid=lti-key-dev`) — each
uvicorn worker generates its own in-memory RSA key that resets on restart.
Launches still work (we verify the *LMS's* token), but **deep-linking and
AGS grade-passback fail** because those rely on the LMS verifying *our*
signature against a stable JWKS. Fix once, before registering any LMS.

Run on the prod server (key is generated there — it never leaves the box):

```bash
cd /root/proctor-browser
grep -q '^LTI_PRIVATE_KEY=' .env && echo "ALREADY SET — stop" || {
  openssl genrsa -out /tmp/lti_priv.pem 2048
  printf 'LTI_KID=lti-prod-1\nLTI_PRIVATE_KEY=%s\n' "$(base64 -w0 /tmp/lti_priv.pem)" >> .env
  shred -u /tmp/lti_priv.pem
  docker compose up -d api
}
```

Verify (should show `kid":"lti-prod-1`, not `lti-key-dev`):

```bash
curl -s https://app.procta.net/lti/jwks
```

Rotation later: add `LTI_NEXT_PRIVATE_KEY` + `LTI_NEXT_KID` (both keys are
published in JWKS; signing uses the primary). Never delete a key the LMS
may still be verifying against until you've confirmed re-fetch.

---

## Phase 2 — Get the org_id to bind LTI users to

Every LTI registration MUST declare which Procta org owns it; users
provisioned from that LMS are stamped with this `org_id` (tenant
isolation). A registration without `org_id` is rejected at launch (403).

On prod, pick the owning org:

```sql
SELECT id, name, slug FROM organizations ORDER BY created_at;
```

Use that UUID as `<ORG_ID>` below.

---

## Phase 3 — Register Procta in the LMS

Give the LMS these tool URLs (already live):

| Field | Value |
|---|---|
| OIDC login initiation | `https://app.procta.net/lti/login` |
| Target link / redirect URI | `https://app.procta.net/lti/launch` |
| Deep linking URL | `https://app.procta.net/lti/deeplink` |
| Public JWKS URL | `https://app.procta.net/lti/jwks` |

### Canvas
1. Admin → Developer Keys → **+ LTI Key** → Configure manually.
2. Set Redirect URIs = `https://app.procta.net/lti/launch`, OpenID Connect
   Initiation Url = `https://app.procta.net/lti/login`, JWK Method = Public
   JWK URL = `https://app.procta.net/lti/jwks`.
3. Placements: Course Navigation / Assignment (+ Deep Linking if used).
4. Save → toggle the key **ON** → copy the **Client ID** (a number).
5. Install in a course/account → note the **Deployment ID**.
6. Collect:
   - issuer: `https://canvas.instructure.com` (or your Canvas host's issuer)
   - client_id: the Developer Key client id
   - deployment_id: from the install
   - auth_login_url: `https://<canvas-host>/api/lti/authorize_redirect`
   - auth_token_url: `https://<canvas-host>/login/oauth2/token`
   - key_set_url: `https://<canvas-host>/api/lti/security/jwks`

### Moodle
1. Site admin → Plugins → External tool → **Configure a tool manually**.
2. Tool URL = `https://app.procta.net/lti/launch`, LTI version = **1.3**,
   Public key type = **Keyset URL** = `https://app.procta.net/lti/jwks`,
   Initiate login URL = `https://app.procta.net/lti/login`, Redirection
   URI(s) = `https://app.procta.net/lti/launch`.
3. Enable IMS LTI Names and Role Provisioning + Assignment and Grade
   Services if you want roster sync / grade passback.
4. Save → open **Tool configuration details** and collect:
   - issuer (Platform ID), client_id, deployment_id,
     auth_login_url (Authentication request URL),
     auth_token_url (Access token URL),
     key_set_url (Public keyset URL).

---

## Phase 4 — Configure LTI_REGISTRATIONS on prod

One JSON **array** holds all platforms (Canvas + Moodle together). Add to
`/root/proctor-browser/.env` as a single line, then reload. Example:

```json
[
  {
    "platform_name": "Canvas",
    "issuer": "https://canvas.instructure.com",
    "client_id": "10000000000123",
    "deployment_ids": ["123:abcdef..."],
    "auth_login_url": "https://canvas.instructure.com/api/lti/authorize_redirect",
    "auth_token_url": "https://canvas.instructure.com/login/oauth2/token",
    "key_set_url": "https://canvas.instructure.com/api/lti/security/jwks",
    "org_id": "<ORG_ID>"
  },
  {
    "platform_name": "Moodle",
    "issuer": "https://moodle.yourschool.edu",
    "client_id": "AbCdEf123",
    "deployment_ids": ["1"],
    "auth_login_url": "https://moodle.yourschool.edu/mod/lti/auth.php",
    "auth_token_url": "https://moodle.yourschool.edu/mod/lti/token.php",
    "key_set_url": "https://moodle.yourschool.edu/mod/lti/certs.php",
    "org_id": "<ORG_ID>"
  }
]
```

```bash
cd /root/proctor-browser
# paste the minified JSON as ONE line:
echo "LTI_REGISTRATIONS='<minified-json-array>'" >> .env
docker compose up -d api
```

Notes:
- `deployment_ids` may be omitted/empty to accept **any** deployment from
  that issuer+client_id (looser — prefer listing them).
- Instructors are provisioned as `org_role='teacher'` (not org admin);
  learners get a hashed `LTI_<sha>` roll. Both stamped with `org_id`.

---

## Phase 5 — Test a real launch

1. In the LMS, open the Procta tool as an **instructor** → expect redirect
   to `/dashboard` (logged in).
2. As a **student** → expect redirect to `/student` (logged in to lobby).
3. Confirm provisioning on prod:

```sql
SELECT full_name, org_role, org_id FROM teachers WHERE lti_user_id IS NOT NULL;
SELECT roll_number, org_id   FROM students WHERE lti_user_id IS NOT NULL;
```

If a launch 401s "Invalid or expired OIDC state" → clock skew or the LMS
took >10 min between login and launch. If 403 "not bound to an
organization" → the registration is missing `org_id`. If signature errors
on deep-link/AGS → Phase 1 key not set (still `lti-key-dev`).

---

## Google Classroom (separate — NOT LTI)

OAuth2 + Classroom API: teachers connect their Classroom from the dashboard
to sync rosters and push grades. Gated on Google OAuth creds.

1. **Google Cloud Console** → new OAuth 2.0 Client (Web application).
   - Authorized redirect URI: `https://app.procta.net/api/v1/google/callback`
2. Enable the **Google Classroom API**.
3. **OAuth consent screen**: the integration uses *restricted* scopes
   (`classroom.rosters.readonly`, `classroom.coursework.students`,
   `classroom.coursework.me`, `classroom.courses.readonly`). Restricted
   scopes require **Google app verification** (privacy policy URL, demo
   video, and a security assessment for restricted scopes). Until verified,
   only test users added on the consent screen can connect. **This is the
   long pole — start it early.**
4. Add to prod `.env`:

```bash
echo "GOOGLE_CLASSROOM_CLIENT_ID=<client-id>" >> .env
echo "GOOGLE_CLASSROOM_CLIENT_SECRET=<client-secret>" >> .env
# redirect URI defaults to https://app.procta.net/api/v1/google/callback
docker compose up -d api
```

5. Verify: the dashboard **Integrations → Google Classroom → Connect**
   button appears (it's hidden when unconfigured), and the OAuth round-trip
   completes back to `/api/v1/google/callback`.

---

## Status checklist

- [ ] Phase 1 — persistent LTI key (`kid=lti-prod-1` in JWKS)
- [ ] Phase 2 — org_id chosen
- [ ] Phase 3 — tool registered in Canvas
- [ ] Phase 3 — tool registered in Moodle
- [ ] Phase 4 — `LTI_REGISTRATIONS` set + reloaded
- [ ] Phase 5 — instructor + student launch verified on prod
- [ ] Google — OAuth client + Classroom API + consent screen
- [ ] Google — restricted-scope verification submitted
- [ ] Google — creds in `.env`, Connect button live
