# Dashboard parity — API endpoints checklist

Auto-extracted from dashboard-app.js + dashboard.html. **107 endpoint+method entries** across 19 groups.
Check each off when the rebuilt (vanilla, on Stitch HTML) section wires it correctly.


## /api/v1/admin  (64)
- [ ] `GET    /api/v1/admin/access-code`
- [ ] `GET    /api/v1/admin/all-orgs`
- [ ] `GET    /api/v1/admin/all-teachers`
- [ ] `GET    /api/v1/admin/analytics`
- [ ] `GET    /api/v1/admin/appeals`
- [ ] `GET    /api/v1/admin/audio-keywords`
- [ ] `GET    /api/v1/admin/backfill-risk-scores`
- [ ] `GET    /api/v1/admin/batches/email-cohort-link`
- [ ] `GET    /api/v1/admin/clear-live-sessions`
- [ ] `GET    /api/v1/admin/coding-question`
- [ ] `GET    /api/v1/admin/coding-question/generate`
- [ ] `GET    /api/v1/admin/exam`
- [ ] `GET    /api/v1/admin/exam-schedule`
- [ ] `GET    /api/v1/admin/exams`
- [ ] `GET    /api/v1/admin/grade-confirm`
- [ ] `GET    /api/v1/admin/grade-confirm-bulk`
- [ ] `GET    /api/v1/admin/grade-suggest`
- [ ] `GET    /api/v1/admin/grading-audit`
- [ ] `GET    /api/v1/admin/groups`
- [ ] `GET    /api/v1/admin/guardian/send-request`
- [ ] `GET    /api/v1/admin/id-decision`
- [ ] `GET    /api/v1/admin/invites`
- [ ] `GET    /api/v1/admin/invites/cap-reset`
- [ ] `GET    /api/v1/admin/invites/cap-status`
- [ ] `GET    /api/v1/admin/invites/resend-bounced`
- [ ] `GET    /api/v1/admin/invites/send`
- [ ] `GET    /api/v1/admin/issues`
- [ ] `GET    /api/v1/admin/lint-questions`
- [ ] `GET    /api/v1/admin/live-monitor`
- [ ] `GET    /api/v1/admin/orgs`
- [ ] `GET    /api/v1/admin/pending-grades`
- [ ] `GET    /api/v1/admin/pending-verifications`
- [ ] `GET    /api/v1/admin/phone-camera-config`
- [ ] `GET    /api/v1/admin/proctoring-sensitivity`
- [ ] `GET    /api/v1/admin/question-bank`
- [ ] `GET    /api/v1/admin/question-bank/export`
- [ ] `GET    /api/v1/admin/question-bank/extract`
- [ ] `GET    /api/v1/admin/question-bank/extract/confirm`
- [ ] `GET    /api/v1/admin/question-bank/generate`
- [ ] `GET    /api/v1/admin/question-bank/generate-from-file`
- [ ] `GET    /api/v1/admin/question-bank/import`
- [ ] `GET    /api/v1/admin/question-bank/suggest-tags`
- [ ] `GET    /api/v1/admin/question-bank/to-exam`
- [ ] `GET    /api/v1/admin/questions`
- [ ] `GET    /api/v1/admin/register-students-bulk`
- [ ] `GET    /api/v1/admin/registered-count`
- [ ] `GET    /api/v1/admin/require-2fa`
- [ ] `GET    /api/v1/admin/sar/export`
- [ ] `GET    /api/v1/admin/scorecard-pdf`
- [ ] `GET    /api/v1/admin/scorecard-zip`
- [ ] `GET    /api/v1/admin/session`
- [ ] `GET    /api/v1/admin/sessions`
- [ ] `GET    /api/v1/admin/shuffle-config`
- [ ] `GET    /api/v1/admin/student-batches`
- [ ] `GET    /api/v1/admin/students`
- [ ] `GET    /api/v1/admin/students/csv-template`
- [ ] `GET    /api/v1/admin/students/import-csv`
- [ ] `GET    /api/v1/admin/students/roster`
- [ ] `GET    /api/v1/admin/teachers`
- [ ] `GET    /api/v1/admin/timeline`
- [ ] `GET    /api/v1/admin/timeline/null`
- [ ] `GET    /api/v1/admin/upload-question-image`
- [ ] `GET    /api/v1/admin/violations/bulk-dismiss`
- [ ] `GET    /api/v1/admin/violations/clusters`

## /api/v1/admin-cleanup  (1)
- [ ] `GET    /api/v1/admin-cleanup`

## /api/v1/admin-failed-sessions  (1)
- [ ] `GET    /api/v1/admin-failed-sessions`

## /api/v1/admin-submit  (1)
- [ ] `GET    /api/v1/admin-submit`

## /api/v1/auth  (12)
- [ ] `GET    /api/v1/auth/2fa/disable`
- [ ] `GET    /api/v1/auth/2fa/enable`
- [ ] `GET    /api/v1/auth/2fa/status`
- [ ] `GET    /api/v1/auth/csrf`
- [ ] `GET    /api/v1/auth/login`
- [ ] `GET    /api/v1/auth/logout`
- [ ] `GET    /api/v1/auth/me`
- [ ] `GET    /api/v1/auth/password-reset`
- [ ] `GET    /api/v1/auth/reauth`
- [ ] `GET    /api/v1/auth/refresh`
- [ ] `GET    /api/v1/auth/sessions`
- [ ] `GET    /api/v1/auth/sessions/revoke-others`

## /api/v1/billing  (7)
- [ ] `GET    /api/v1/billing/cancel`
- [ ] `GET    /api/v1/billing/change-plan`
- [ ] `GET    /api/v1/billing/create-subscription`
- [ ] `GET    /api/v1/billing/invoices`
- [ ] `GET    /api/v1/billing/onboarding-status`
- [ ] `GET    /api/v1/billing/usage`
- [ ] `GET    /api/v1/billing/validate-coupon`

## /api/v1/export-csv  (1)
- [ ] `GET    /api/v1/export-csv`

## /api/v1/export-excel  (1)
- [ ] `GET    /api/v1/export-excel`

## /api/v1/export-pdf  (1)
- [ ] `GET    /api/v1/export-pdf`

## /api/v1/google  (5)
- [ ] `GET    /api/v1/google`
- [ ] `GET    /api/v1/google/auth`
- [ ] `GET    /api/v1/google/courses`
- [ ] `GET    /api/v1/google/disconnect`
- [ ] `GET    /api/v1/google/sync-roster`

## /api/v1/issues  (1)
- [ ] `GET    /api/v1/issues`

## /api/v1/notification-preferences  (1)
- [ ] `GET    /api/v1/notification-preferences`

## /api/v1/org  (4)
- [ ] `GET    /api/v1/org`
- [ ] `GET    /api/v1/org/billing`
- [ ] `GET    /api/v1/org/invite`
- [ ] `GET    /api/v1/org/members`

## /api/v1/public-config  (1)
- [ ] `GET    /api/v1/public-config`

## /api/v1/results  (1)
- [ ] `GET    /api/v1/results`

## /api/v1/sse  (2)
- [ ] `GET    /api/v1/sse/connect-token`
- [ ] `GET    /api/v1/sse/sessions`

## /api/v1/student-history  (1)
- [ ] `GET    /api/v1/student-history`

## /api/v1/student-search  (1)
- [ ] `GET    /api/v1/student-search`

## /api/v1/templates  (1)
- [ ] `GET    /api/v1/templates`
