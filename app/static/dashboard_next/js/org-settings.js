// Org Settings admin — load + save the org name. GET /api/v1/org -> {name,slug,max_students};
// PATCH /api/v1/org {name}. (Strictness/retention/danger-zone in the design have no org-level
// endpoints yet — left as UI; wire when the backend lands.)
(function () {
  const api = window.ProctaAPI; if (!api) return;
  const { authFetch, onAction } = api;
  const $ = (id) => document.getElementById(id);
  (async function () {
    try {
      const r = await authFetch("/api/v1/org"); if (!r.ok) return;
      const d = await r.json().catch(() => ({}));
      if ($("org-name")) $("org-name").value = d.name || "";
      if ($("org-slug")) $("org-slug").value = d.slug || "";
    } catch (_) {}
  })();
  onAction("saveOrg", async (btn) => {
    const name = ($("org-name") || {}).value || "";
    if (!name.trim()) { alert("Organization name is required."); return; }
    btn.disabled = true; const prev = btn.textContent; btn.textContent = "Saving…";
    try {
      const r = await authFetch("/api/v1/org", { method: "PATCH", body: JSON.stringify({ name: name.trim() }) });
      if (r.ok) btn.textContent = "Saved ✓"; else { const d = await r.json().catch(() => ({})); alert("Save failed: " + (d.detail || r.status)); btn.textContent = prev; }
      setTimeout(() => { btn.textContent = prev; btn.disabled = false; }, 1500);
    } catch (_) { alert("Save failed."); btn.textContent = prev; btn.disabled = false; }
  });
})();
