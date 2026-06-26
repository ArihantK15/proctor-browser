// Settings — vanilla, CSP-safe. Tab switching (ported from the export's inline
// switchTab), profile display from /auth/me, and the DPDP data controls:
//   GET  /api/v1/auth/me
//   GET  /api/v1/privacy/export   (download JSON — DPDP §11 / GDPR Art 15+20)
//   POST /api/v1/privacy/delete   (DPDP §13 / GDPR Art 17 — requires reauth token)
(function () {
  const api = window.ProctaAPI; if (!api) return;
  const { authFetch, onAction } = api;
  const $ = (id) => document.getElementById(id);
  const TABS = ["account", "exam", "proctoring", "integrations", "maintenance", "danger"];

  // ---- tab switching (account = real content, others = under-development placeholder) ----
  onAction("settingsTab", (el) => {
    const tabId = el.getAttribute("data-tab");
    TABS.forEach((t) => {
      const btn = $(`tab-${t}`); if (!btn) return;
      const on = t === tabId;
      btn.classList.toggle("bg-surface-container-high", on);
      btn.classList.toggle("border-primary/20", on);
      btn.classList.toggle("active-glow", on);
      btn.classList.toggle("text-primary", on);
      btn.classList.toggle("hover:bg-surface-container-high", !on);
      btn.classList.toggle("text-on-surface-variant", !on);
      const icon = btn.querySelector(".material-symbols-outlined"); if (icon) icon.classList.toggle("text-primary", on);
      const label = btn.querySelector(".font-body-base"); if (label) label.classList.toggle("font-semibold", on);
    });
    const acct = $("section-account"), other = $("section-other");
    if (acct) acct.classList.toggle("hidden", tabId !== "account");
    if (other) other.classList.toggle("hidden", tabId === "account");
  });

  // ---- profile ----
  (async function () {
    try {
      const r = await authFetch("/api/v1/auth/me");
      if (!r.ok) return;
      const me = await r.json().catch(() => ({}));
      const n = $("set-name"); if (n) n.value = me.full_name || "";
      const e = $("set-email"); if (e) e.value = me.email || "";
    } catch (_) {}
  })();

  onAction("changePassword", () => {
    alert("To change your password, use the “Forgot password” reset flow from the login screen — a verified reset link will be emailed to you.");
  });

  // ---- DPDP export ----
  onAction("exportData", async (el) => {
    const prev = el.textContent; el.textContent = "Preparing…"; el.disabled = true;
    try {
      const r = await authFetch("/api/v1/privacy/export");
      if (!r.ok) { alert("Export failed (HTTP " + r.status + ")."); return; }
      const blob = await r.blob();
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url; a.download = "procta-data-export.json";
      document.body.appendChild(a); a.click(); a.remove();
      setTimeout(() => URL.revokeObjectURL(url), 1000);
    } catch (_) { alert("Export failed."); }
    finally { el.textContent = prev; el.disabled = false; }
  });

  // ---- DPDP delete (server requires reauth; surface that honestly) ----
  onAction("deleteAllRecords", async () => {
    if (!window.confirm("Permanently delete your account and anonymise all associated exam history? This cannot be undone.")) return;
    try {
      const r = await authFetch("/api/v1/privacy/delete", { method: "POST", body: JSON.stringify({}) });
      if (r.ok) { alert("Account deletion processed. You will be signed out."); window.location.href = "/"; return; }
      const d = await r.json().catch(() => ({}));
      if (r.status === 401 || r.status === 403) {
        alert("For your security, deleting all records requires re-verifying your identity (password re-entry). " + (d.detail || "Please re-authenticate and try again."));
      } else {
        alert("Deletion failed: " + (d.detail || ("HTTP " + r.status)));
      }
    } catch (_) { alert("Deletion failed."); }
  });
})();
