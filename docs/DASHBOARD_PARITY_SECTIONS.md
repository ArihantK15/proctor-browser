# Dashboard parity — sections × features × edge-cases

Definition-of-done for the vanilla rebuild on Stitch HTML. Pairs with
`DASHBOARD_PARITY_ENDPOINTS.md`. 17 sections; 144 `data-action` triggers grounded from
dashboard.html. Check each off when the rebuilt section behaves identically. **No cutover until 100%.**

## Cross-cutting (every section must honor)
- [ ] **CSP-safe**: no inline `<script>`/`onclick`; external `/static/*.js` + `data-action` delegation only (`script-src 'self'`).
- [ ] **Auth**: cookie-session + `X-CSRF-Token` on all mutations (`credentials:'include'`).
- [ ] **Theme**: `setTheme` light/dark toggle works across all screens.
- [ ] **Tabs**: `switchTab` shows/hides sections; role-gated tabs hidden for non-admin/superadmin.
- [ ] **Onboarding**: `onboardOpen/Next/Prev/Skip` tour; first-run empty states.
- [ ] **No regressions of this session's fixes** (see each section).

## questions (authoring — the richest)
- [ ] List + `addQuestion`, `saveQuestions`, `qExpandAll/qCollapseAll`, `setQTypeFilter`, `loadQuestions`
- [ ] Types: MCQ single/multi, true/false (fixed options), numeric (tolerance), short-answer, **coding**
- [ ] Coding **wizard** (`showCodingWizard`, `cwizNext/cwizBack`, 5 steps Problem/Languages/Examples/Hidden/Review) + `cwizUseAdvanced` **carries the draft** (no data loss)
- [ ] Coding **advanced** (`codingSave`, `codingAddTestCase`, `codingToggleLang`, per-language starter tabs)
- [ ] AI gen (`doGenerateQuestions`, `codingShowGenPrompt`, `setAITab`) — "AI-drafted, review before save"
- [ ] Lint/rubrics (`lintQuestions`), PDF/Word import (`qbankPdfPick/qbankGenFilePick/qbankExtractConfirm`)
- [ ] Question bank (`toggleBank`, `bankToExam`, `doBankImport`, `exportBank`, `downloadBankCSVTemplate`)
- [ ] **EDGE:** wizard double-create guard (`_cwizBusy`); float-tolerance validated (Number, ≥0) not silently dropped; use `escAttr` (not `_escAttr`); blank starter `""` preserved; marks 1–100 / time 1–15 clamp.
- [ ] **NEW extras:** Preview Environment (post-parity), AI Collection Builder (post-parity).

## live (Live Monitor)
- [ ] Session grid + risk pills (0–100, ≤30/31–70/>70), `sortLive`, `refreshLive`, filters All/Live/Flagged/Completed
- [ ] **SSE live feed**; `viewRiskDetail` explainable breakdown; `openTimeline`; `setTimeExtension`
- [ ] Camera **Peek** on-demand (NOT live-streamed); `closeLiveView`; room cam (`closeRoomCamView`, `roomCamApprove/Reject`, `closeSecondaryCamGrid`)
- [ ] Broadcast/chat (`openBroadcastModal`, `sendBroadcast`); `toggleAlertMute`; `switchTabLiveClearBadge`
- [ ] **EDGE:** SSE must **reconnect on exam switch** (`_sseExamId` guard — recurring "live filtering by exam broken" bug); poll path masks it.
- [ ] **NEW:** Historical Log view (bake in).

## results
- [ ] Results table (score/risk/pass-fail/violations), `sortResults`, `refreshResults`, configurable pass-mark
- [ ] Exports: `exportCSV`, `exportExcel`, `downloadPDF`, `dlAllScorecards`, `emailAllScorecards`
- [ ] Grading: `openGradeReview`, `runGradeSuggest` (AI short-answer **suggestion**, teacher confirms), `gradeBulkAccept/Reject`, `loadPendingGrades`, `toggleGradeAudit`
- [ ] Cluster review/triage (`openTriage`, `_triageOpenTimeline`) — bulk-dismiss systemic false positives
- [ ] **EDGE:** double-canon score bug avoided; AI grade is advisory only; per-row update (no bulk-rollback).

## students
- [ ] Roster table (name, **roll** monospace, email, group), `refreshStudentList`, `removeStudentFromRoster` (keeps login; confirm)
- [ ] Groups (`createGroup`, `addGroupMembers`, `assignGroupToExam`, `assignBatchToExam`, `closeGroupDetail`)
- [ ] **Bulk import**: `bulkImportPreview` (dry-run) + `bulkImportConfirm`, roll-format detect (CBSE/JEE/NTA), `downloadImportTemplate`
- [ ] Registration link (`copyLink`, `copyCohortLink`, `emailCohortLink`); invites (`sendInvites`, `loadInvites`, `resendBouncedInvites`, `resetInviteCap`, `pullGroupIntoInvites`)
- [ ] **NEW:** QR self-registration card + "Pending registration" count (bake in — QR of existing reg link).
- [ ] **EDGE:** two-level identity (email=account global-unique; (teacher,roll)=roster, roll per-teacher); same-email/diff-roll typo warning.

## exams
- [ ] `createExam`/`showCreateExamModal`, `duplicateCurrentExam`, `archiveCurrentExam/unarchiveCurrentExam`, `confirmDeleteExam`
- [ ] Schedule (`saveSchedule/clearSchedule`), access code (`generateAccessCode/saveAccessCode/clearAccessCode`), templates (`saveTemplate`)
- [ ] Proctoring config (`saveSensitivity`, `saveAudioKeywords`), room-camera toggle, early-join window, `setTimeExtension`
- [ ] Status pills Draft/Scheduled/Live/Completed/Archived.

## billing
- [ ] Plan + usage/quota (soft-cap), overage; `upgradePlan`, `cancelSubscription`, `openBillingPortal`
- [ ] Coupons (`applyCoupon`, `applyCouponBilling`), `showUpgradeModal`, `trialBannerClick`
- [ ] Razorpay/UPI Autopay; invoices (PDF); plans match `constants.py:PLANS` (no drift).
- [ ] **EDGE:** orphan/double-charge guards; webhook idempotency; status-gate (the #183/#184 fixes).

## integrations
- [ ] Google Classroom: `connectGoogle/disconnectGoogle`, `syncGoogleRoster` (live); PKCE; scope note
- [ ] LTI 1.3 **(Beta)** config URLs; Razorpay card; `reloadExtensions`
- [ ] **EDGE:** Classroom OAuth in Testing (test users); sync-roster currently emails no one.

## tools / settings
- [ ] `showToolsSection` two-pane (students/exam/integrations/maintenance/danger); `setTheme`
- [ ] Maintenance: `doCleanup`, `clearLiveSessionsStep`, `doBackfill`, `loadFailed`
- [ ] Security: `enable2FA/disable2FA`, `revokeOtherSessions`
- [ ] **Danger zone:** "Privacy & your data" → `/privacy` (export/delete, DPDP) — the link we added.

## issues
- [ ] `openIssueReport/submitIssueReport/closeIssueReport`, `loadIssues` (teacher issue reports + appeals).

## review
- [ ] `loadReview`, grade review (`openGradeReview`, `gradeBulkAccept/Reject`), secondary-cam approval grid (`roomCamApprove/Reject`, `closeSecondaryCamGrid`); appeals (`loadAppeals`).

## history
- [ ] `sortHistory`, `openTimeline`, `closeHistoryDetail` — student/session history timeline.

## chat
- [ ] Broadcast (`openBroadcastModal/sendBroadcast/closeBroadcastModal`) + per-student reply during exam.
- [ ] **EDGE:** WS handlers need RLS context; student reauth must match the connection's roll; reconnect race.

## members + org + org-settings  (org admin only)
- [ ] Members: `showInviteTeacherModal`, `doInviteTeacher`; teacher list + per-teacher roll-up; reassign/offboard
- [ ] Org: `loadOrgLiveMonitor` (cross-teacher live), org roll-up KPIs
- [ ] Org-settings: `saveOrgName`, `setOrgRequire2fa`; manager-only gating; strict per-teacher isolation.

## all-orgs + debug + security  (superadmin only)
- [ ] all-orgs: cross-org monitoring/roll-up; debug: ops/diagnostics; security: audit/2FA-enforcement
- [ ] Privacy/SAR: `sarExport` (operator data export/delete)
- [ ] **EDGE:** `require_admin` 403s superadmin on mutations unless allow-listed (`_SUPERADMIN_WRITE_ALLOW`) — superadmin write endpoints are dead-on-arrival unless allow-listed.

## privacy (self-service /privacy — standalone)
- [ ] Export my data, Delete my account (reauth), consent, object-to-processing — DPDP. (Already built; matches.)
