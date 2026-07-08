// Unified sign-in for students AND teachers. One page, a role toggle, the two
// existing login endpoints. CSP-safe: external file, no inline handlers.
//
//   Student : POST /api/v1/student/auth/login {email,password,captcha_token}
//             403 {detail:{code:"EMAIL_VERIFICATION_REQUIRED"}} -> verify email
//             200 -> sets student cookies -> /student-dashboard
//   Teacher : POST /api/v1/auth/login {email,password,captcha_token,email_otp_code?}
//             401 {error:"EMAIL_2FA_REQUIRED"} -> reveal OTP, resubmit with code
//             200 -> sets teacher cookies -> /dashboard
//             (was /dashboard-next until 2026-07-01 — that's the unfinished
//             rebuild reverted to non-default on 2026-06-27; every teacher
//             login through this page landed on it by mistake since this
//             file was written, /dashboard-next never actually ran an auth
//             check so nothing ever visibly failed)
//
// Both endpoints verify a Cloudflare Turnstile token (single-use, so we reset
// the widget after every attempt). Sign-UP is intentionally NOT here — it is
// role-specific (students /register against a roster; teachers sign up an org
// at procta.net) — so we link out to the right place per role.
(function () {
  "use strict";
  var $ = function (id) { return document.getElementById(id); };

  // Best-effort diagnostic report for pre-auth failures (see
  // /api/v1/client-diagnostic's docstring). Fire-and-forget: if the network
  // is ALSO too broken to send this, that's fine — it's not on the critical
  // path and must never throw or block the user-facing error message.
  async function reportClientDiagnostic(context, err, target) {
    try {
      var appVersion = "";
      if (window.procta_native && typeof window.procta_native.getAppVersion === "function") {
        try { appVersion = await window.procta_native.getAppVersion(); } catch (_) {}
      }
      var body = JSON.stringify({
        context: context,
        error_name: (err && err.name) || "",
        error_message: (err && err.message) || String(err || ""),
        target: (target || "").split("?")[0],
        app_version: appVersion || "",
        platform: navigator.platform || "",
      });
      fetch("/api/v1/client-diagnostic", {
        method: "POST", headers: { "Content-Type": "application/json" }, body: body,
      }).catch(function () {});
    } catch (_) {}
  }

  var ROLE_KEY = "procta_login_role";
  var ROLES = {
    student: {
      endpoint: "/api/v1/student/auth/login",
      reset: "/api/v1/student/auth/reset-request",
      dest: "/student-dashboard",
      sub: "Access your upcoming exams and results.",
      signup: '<span class="muted">New here?</span> <a href="/register">Register for your exam</a>',
    },
    teacher: {
      endpoint: "/api/v1/auth/login",
      reset: "/api/v1/auth/password-reset",
      dest: "/dashboard",
      sub: "Sign in to manage exams and monitor sessions.",
      signup: '<span class="muted">Need an account?</span> <a href="https://procta.net/signup">Create one</a>',
    },
  };

  var role = "student";
  var siteKey = "";
  var turnstileEnabled = false;
  var widgetId = null;
  var captchaToken = "";

  // ---- Turnstile ---------------------------------------------------------
  function renderTurnstile() {
    var box = $("cf-turnstile");
    if (!siteKey || !window.turnstile || !box || widgetId !== null) return;
    try {
      widgetId = window.turnstile.render(box, {
        sitekey: siteKey,
        theme: "dark",
        callback: function (t) { captchaToken = t || ""; },
        "expired-callback": function () { captchaToken = ""; },
        "error-callback": function () { captchaToken = ""; },
      });
    } catch (_) {}
  }
  function resetTurnstile() {
    captchaToken = "";
    if (widgetId !== null && window.turnstile) {
      try { window.turnstile.reset(widgetId); } catch (_) {}
    }
  }
  // Fetch the public Turnstile site key. Empty key => Turnstile is DISABLED
  // (backend runs in sandbox mode and verify_or_403 passes through), so we hide
  // the widget slot and must NOT gate sign-in on a token — otherwise login is
  // impossible (no widget => no token ever). When a key is present, render the
  // widget once the async Turnstile script lands.
  function disableTurnstile() { turnstileEnabled = false; var box = $("cf-turnstile"); if (box) box.style.display = "none"; }
  fetch("/api/v1/public-config", { credentials: "include" })
    .then(function (r) { return r.ok ? r.json() : {}; })
    .then(function (d) {
      siteKey = (d && d.turnstile_site_key) || "";
      if (!siteKey) { disableTurnstile(); return; }
      turnstileEnabled = true;
      if (window.turnstile) { renderTurnstile(); return; }
      var n = 0, iv = setInterval(function () {
        if (window.turnstile || n++ > 40) { clearInterval(iv); renderTurnstile(); }
      }, 150);
    })
    .catch(function () { disableTurnstile(); });

  // ---- Role toggle -------------------------------------------------------
  function safeNext(raw) {
    // Same-origin internal paths only. Resolve against our own origin and reject
    // anything that escapes it — this kills absolute URLs, "//host", "/\host"
    // (browsers fold "\"→"/"), and scheme tricks like "javascript:" in one check
    // (and, unlike a charAt allowlist, CodeQL recognises the origin compare as a
    // sanitizer). Returns only the path+query+hash so the redirect can never be a
    // full URL or a non-http(s) scheme.
    if (!raw) return "";
    try {
      var u = new URL(raw, location.origin);
      if (u.origin !== location.origin) return "";
      var path = u.pathname + u.search + u.hash;
      if (path.charAt(0) !== "/" || path.charAt(1) === "/") return "";
      return path;
    } catch (_) { return ""; }
  }
  var qs = new URLSearchParams(location.search);
  var nextDest = safeNext(qs.get("next"));

  function setRole(r) {
    role = ROLES[r] ? r : "student";
    try { localStorage.setItem(ROLE_KEY, role); } catch (_) {}
    $("seg-student").classList.toggle("active", role === "student");
    $("seg-teacher").classList.toggle("active", role === "teacher");
    $("seg-student").setAttribute("aria-selected", role === "student");
    $("seg-teacher").setAttribute("aria-selected", role === "teacher");
    $("sub").textContent = ROLES[role].sub;
    $("signup-link").innerHTML = ROLES[role].signup;
    // Teacher 2FA only applies to teachers; hide the OTP step on switch.
    $("otp-wrap").hidden = true; $("otp").value = "";
    clearMsg();
  }

  // ---- Messages ----------------------------------------------------------
  function showErr(t) { var e = $("err"); e.textContent = t; e.hidden = false; $("ok").hidden = true; }
  function showOk(t) { var e = $("ok"); e.innerHTML = t; e.hidden = false; $("err").hidden = true; }
  function clearMsg() { $("err").hidden = true; $("ok").hidden = true; }

  function setBusy(b, label) {
    var btn = $("submit");
    btn.disabled = b;
    btn.innerHTML = b ? '<span class="spin"></span>' : (label || "Sign in");
  }

  // ---- Submit ------------------------------------------------------------
  async function submit(e) {
    if (e) e.preventDefault();
    clearMsg();
    var email = $("email").value.trim();
    var password = $("password").value;
    if (!email || email.indexOf("@") < 0) { showErr("Enter a valid email address."); return; }
    if (!password) { showErr("Enter your password."); return; }
    if (turnstileEnabled && !captchaToken) { showErr("Please complete the verification challenge."); return; }

    var cfg = ROLES[role];
    var body = { email: email, password: password, captcha_token: captchaToken };
    var otpVisible = !$("otp-wrap").hidden;
    if (role === "teacher" && otpVisible) {
      var code = $("otp").value.trim();
      if (!/^[0-9]{6}$/.test(code)) { showErr("Enter the 6-digit code from your email."); return; }
      body.email_otp_code = code;
    }

    setBusy(true);
    var r, data;
    try {
      r = await fetch(cfg.endpoint, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        credentials: "include",
        body: JSON.stringify(body),
      });
    } catch (err) {
      // Previously this discarded `err` entirely — every prior attempt to
      // diagnose a recurring "Failed to fetch" report here had zero real
      // data to work from (confirmed 2026-07-08: nothing reached Sentry,
      // nothing was logged anywhere). Best-effort report so the NEXT
      // occurrence is actually queryable.
      reportClientDiagnostic("login_submit", err, cfg.endpoint);
      setBusy(false); resetTurnstile();
      showErr("Network error. Check your connection and try again.");
      return;
    }
    try { data = await r.json(); } catch (_) { data = {}; }

    if (r.ok) {
      showOk("Signed in. Redirecting…");
      // nextDest is safeNext()-sanitised: resolved against location.origin and
      // rejected unless it's a same-origin path (kills //host, /\host, absolute
      // URLs and javascript: — see safeNext + the 8-vector test). CodeQL accepts
      // it; Semgrep's community open-redirect rule is purely syntactic (flags any
      // location.href = <var>), so we suppress that one rule on this verified line.
      location.href = nextDest || cfg.dest; // nosemgrep: javascript.browser.security.open-redirect.js-open-redirect
      return;
    }

    setBusy(false);
    resetTurnstile();

    // Teacher email-2FA challenge.
    if (role === "teacher" && (data.error === "EMAIL_2FA_REQUIRED" || (data.detail && data.detail.code === "EMAIL_2FA_REQUIRED"))) {
      $("otp-wrap").hidden = false; $("otp").focus();
      showOk(data.message || (data.detail && data.detail.message) || "We sent a 6-digit code to your email. Enter it to finish signing in.");
      return;
    }
    // Student email-verification required before first login.
    var code2 = (data.detail && data.detail.code) || data.code;
    if (role === "student" && code2 === "EMAIL_VERIFICATION_REQUIRED") {
      showErr((data.detail && data.detail.message) || "Check your email for a 6-digit verification code before signing in.");
      return;
    }
    if (r.status === 429) {
      showErr(detailText(data) || "Too many attempts. Please wait a few minutes and try again.");
      return;
    }
    showErr(detailText(data) || "Invalid email or password.");
  }

  function detailText(data) {
    if (!data) return "";
    if (typeof data.detail === "string") return data.detail;
    if (data.detail && typeof data.detail.message === "string") return data.detail.message;
    if (typeof data.message === "string") return data.message;
    return "";
  }

  // ---- Forgot password ---------------------------------------------------
  async function forgot(e) {
    if (e) e.preventDefault();
    clearMsg();
    var email = $("email").value.trim();
    if (!email || email.indexOf("@") < 0) { showErr("Enter your email above first, then click “Forgot password?”"); $("email").focus(); return; }
    if (turnstileEnabled && !captchaToken) { showErr("Complete the verification challenge first, then click “Forgot password?”"); return; }
    var cfg = ROLES[role];
    setBusy(true, "Sign in");
    try {
      await fetch(cfg.reset, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        credentials: "include",
        body: JSON.stringify({ email: email, captcha_token: captchaToken }),
      });
    } catch (_) {}
    setBusy(false); resetTurnstile();
    // Neutral response regardless of account existence (no enumeration). The
    // Turnstile token was single-use and is now spent, so the widget reset above
    // hands out a fresh challenge — flag that so signing in here doesn't feel
    // broken when it asks for verification again.
    showOk("If an account exists for that email, we’ve sent password-reset instructions — check your inbox. To sign in here, complete the verification again.");
  }

  // ---- Password show/hide ------------------------------------------------
  var EYE = '<path d="M2 12s3.5-7 10-7 10 7 10 7-3.5 7-10 7-10-7-10-7z"/><circle cx="12" cy="12" r="3"/>';
  var EYE_OFF = '<path d="M17.94 17.94A10.07 10.07 0 0 1 12 20c-7 0-11-8-11-8a18.45 18.45 0 0 1 5.06-5.94M9.9 4.24A9.12 9.12 0 0 1 12 4c7 0 11 8 11 8a18.5 18.5 0 0 1-2.16 3.19m-6.72-1.07a3 3 0 1 1-4.24-4.24"/><line x1="1" y1="1" x2="23" y2="23"/>';
  function togglePw() {
    var inp = $("password"), btn = $("pw-toggle"), eye = $("pw-eye");
    if (!inp) return;
    var show = inp.type === "password";
    inp.type = show ? "text" : "password";
    if (eye) eye.innerHTML = show ? EYE_OFF : EYE;
    if (btn) { btn.setAttribute("aria-label", show ? "Hide password" : "Show password"); btn.setAttribute("aria-pressed", show ? "true" : "false"); }
    inp.focus();
  }

  // ---- Wire up -----------------------------------------------------------
  $("seg-student").addEventListener("click", function () { setRole("student"); });
  $("seg-teacher").addEventListener("click", function () { setRole("teacher"); });
  $("login-form").addEventListener("submit", submit);
  $("forgot").addEventListener("click", forgot);
  if ($("pw-toggle")) $("pw-toggle").addEventListener("click", togglePw);

  // Initial role: ?role= param > last-used > student.
  var initial = qs.get("role");
  if (!ROLES[initial]) { try { initial = localStorage.getItem(ROLE_KEY); } catch (_) { initial = null; } }
  setRole(ROLES[initial] ? initial : "student");
})();
