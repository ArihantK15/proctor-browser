// Unified sign-in for students AND teachers. One page, a role toggle, the two
// existing login endpoints. CSP-safe: external file, no inline handlers.
//
//   Student : POST /api/v1/student/auth/login {email,password,captcha_token}
//             403 {detail:{code:"EMAIL_VERIFICATION_REQUIRED"}} -> verify email
//             200 -> sets student cookies -> /student-next
//   Teacher : POST /api/v1/auth/login {email,password,captcha_token,email_otp_code?}
//             401 {error:"EMAIL_2FA_REQUIRED"} -> reveal OTP, resubmit with code
//             200 -> sets teacher cookies -> /dashboard-next
//
// Both endpoints verify a Cloudflare Turnstile token (single-use, so we reset
// the widget after every attempt). Sign-UP is intentionally NOT here — it is
// role-specific (students /register against a roster; teachers sign up an org
// at procta.net) — so we link out to the right place per role.
(function () {
  "use strict";
  var $ = function (id) { return document.getElementById(id); };

  var ROLE_KEY = "procta_login_role";
  var ROLES = {
    student: {
      endpoint: "/api/v1/student/auth/login",
      reset: "/api/v1/student/auth/reset-request",
      dest: "/student-next",
      sub: "Access your upcoming exams and results.",
      signup: '<span class="muted">New here?</span> <a href="/register">Register for your exam</a>',
    },
    teacher: {
      endpoint: "/api/v1/auth/login",
      reset: "/api/v1/auth/password-reset",
      dest: "/dashboard-next",
      sub: "Sign in to manage exams and monitor sessions.",
      signup: '<span class="muted">Need an account?</span> <a href="https://procta.net/">Create one</a>',
    },
  };

  var role = "student";
  var siteKey = "";
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
  // Turnstile's async script may land before or after us; poll briefly.
  (function waitForTurnstile() {
    if (window.turnstile) { renderTurnstile(); return; }
    var n = 0, iv = setInterval(function () {
      if (window.turnstile || n++ > 40) { clearInterval(iv); renderTurnstile(); }
    }, 150);
  })();

  // ---- Role toggle -------------------------------------------------------
  function safeNext(raw) {
    // Only same-origin relative paths; never "//host" or absolute URLs.
    if (!raw || raw.charAt(0) !== "/" || raw.charAt(1) === "/") return "";
    return raw;
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
    if (!captchaToken) { showErr("Please complete the verification challenge."); return; }

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
    } catch (_) {
      setBusy(false); resetTurnstile();
      showErr("Network error. Check your connection and try again.");
      return;
    }
    try { data = await r.json(); } catch (_) { data = {}; }

    if (r.ok) {
      showOk("Signed in. Redirecting…");
      location.href = nextDest || cfg.dest;
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
    if (!captchaToken) { showErr("Complete the verification challenge first, then click “Forgot password?”"); return; }
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
    // Neutral response regardless of account existence (no enumeration).
    showOk("If an account exists for that email, we’ve sent password-reset instructions. Check your inbox.");
  }

  // ---- Wire up -----------------------------------------------------------
  $("seg-student").addEventListener("click", function () { setRole("student"); });
  $("seg-teacher").addEventListener("click", function () { setRole("teacher"); });
  $("login-form").addEventListener("submit", submit);
  $("forgot").addEventListener("click", forgot);

  // Initial role: ?role= param > last-used > student.
  var initial = qs.get("role");
  if (!ROLES[initial]) { try { initial = localStorage.getItem(ROLE_KEY); } catch (_) { initial = null; } }
  setRole(ROLES[initial] ? initial : "student");
})();
