// Integrations — vanilla, CSP-safe. Live Google Classroom connection state + connect/
// disconnect/refresh; copy-chips for the LTI config (LTI itself is dormant, so its
// Client/Deployment IDs remain design placeholders until per-tenant LTI lands).
//   GET  /api/v1/google/courses     -> {connected, email, courses:[...]}
//   GET  /api/v1/google/auth        -> OAuth redirect (connect)
//   POST /api/v1/google/disconnect
(function () {
  const api = window.ProctaAPI; if (!api) return;
  const { authFetch, onAction } = api;
  const $ = (id) => document.getElementById(id);
  let connected = false;

  function setStatus(text, tone) {
    const pill = $("gc-status"); if (!pill) return;
    pill.textContent = text;
    pill.className = `status-pill bg-${tone}/10 text-${tone} border border-${tone}/20`;
  }

  async function refresh() {
    let d = null;
    try { const r = await authFetch("/api/v1/google/courses"); d = r.ok ? await r.json().catch(() => null) : null; } catch (_) {}
    connected = !!(d && d.connected);
    const courses = d && Array.isArray(d.courses) ? d.courses : [];
    if (connected) {
      setStatus("Connected", "secondary");
      const c = $("gc-courses"); if (c) c.textContent = `${courses.length} Active`;
      const ls = $("gc-lastsync"); if (ls) ls.textContent = d.email || "Synced";
      const lbl = $("gc-primary-label"); if (lbl) lbl.textContent = "Refresh Courses";
      const ic = $("gc-primary-icon"); if (ic) ic.textContent = "sync";
    } else {
      setStatus("Not Connected", "outline");
      const c = $("gc-courses"); if (c) c.textContent = "—";
      const ls = $("gc-lastsync"); if (ls) ls.textContent = (d && d.error) ? "Reconnect needed" : "—";
      const lbl = $("gc-primary-label"); if (lbl) lbl.textContent = "Connect Google";
      const ic = $("gc-primary-icon"); if (ic) ic.textContent = "link";
    }
  }

  onAction("gcPrimary", () => {
    if (connected) refresh();
    else window.location.href = "/api/v1/google/auth";
  });

  onAction("gcDisconnect", async () => {
    if (!connected) return;
    if (!window.confirm("Disconnect Google Classroom? Linked courses stay but syncing stops.")) return;
    try { await authFetch("/api/v1/google/disconnect", { method: "POST" }); } catch (_) {}
    refresh();
  });

  // copy the .copy-chip immediately preceding the clicked button
  onAction("copyChip", (el) => {
    const chip = el.parentElement && el.parentElement.querySelector(".copy-chip");
    if (chip && navigator.clipboard) navigator.clipboard.writeText(chip.textContent.trim()).catch(() => {});
    const prev = el.textContent;
    el.textContent = "check";
    setTimeout(() => { el.textContent = prev; }, 1500);
  });

  refresh();
})();
