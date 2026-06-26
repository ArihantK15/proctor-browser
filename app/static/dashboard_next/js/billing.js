// Billing & Usage (org admin). Endpoints:
//   GET  /api/v1/billing/usage     {plan_name,plan_limit,base_price,status,students_used,exam_attempts,overage_amount,...}
//   GET  /api/v1/billing/invoices  {invoices:[{amount(paise),currency,status,created_at,pdf_url,description}]}
//   POST /api/v1/billing/portal-link -> {portal_url}
(function () {
  const api = window.ProctaAPI; if (!api) return;
  const { authFetch, onAction } = api;
  const $ = (id) => document.getElementById(id);
  const esc = (s) => String(s == null ? "" : s).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
  const inr = (n) => "₹" + Number(n || 0).toLocaleString("en-IN");
  const day = (s) => String(s || "").slice(0, 10) || "—";

  async function loadUsage() {
    let d; try { const r = await authFetch("/api/v1/billing/usage"); if (!r.ok) return; d = await r.json(); } catch (_) { return; }
    if ($("bl-plan")) $("bl-plan").textContent = d.plan_name || d.plan_id || "—";
    if ($("bl-status")) $("bl-status").textContent = (d.status || "—").toUpperCase();
    if ($("bl-price")) $("bl-price").textContent = inr(d.base_price);
    if ($("bl-renew")) $("bl-renew").textContent = d.period_start ? ("Current period from " + day(d.period_start)) : " ";
    const used = Number(d.students_used || 0), lim = Number(d.plan_limit || 0);
    if ($("bl-used")) $("bl-used").textContent = used.toLocaleString("en-IN");
    if ($("bl-limit")) $("bl-limit").textContent = "/ " + lim.toLocaleString("en-IN") + " limit";
    const pct = lim > 0 ? Math.min(100, Math.round((used / lim) * 100)) : 0;
    if ($("bl-bar")) $("bl-bar").style.width = pct + "%";
    if ($("bl-cap")) $("bl-cap").textContent = lim > 0 ? (pct + "% of plan limit used") : " ";
    if ($("bl-attempts")) $("bl-attempts").textContent = Number(d.exam_attempts || 0).toLocaleString("en-IN");
    if ($("bl-overage")) $("bl-overage").textContent = Number(d.overage_amount || 0) > 0 ? ("Overage: " + inr(d.overage_amount)) : " ";
  }

  async function loadInvoices() {
    const tb = $("bl-invoices"); if (!tb) return;
    let rows = [];
    try { const r = await authFetch("/api/v1/billing/invoices"); if (r.ok) { const d = await r.json(); rows = Array.isArray(d.invoices) ? d.invoices : []; } } catch (_) {}
    if (!rows.length) { tb.innerHTML = '<tr><td colspan="4" class="p-4 text-on-surface-variant text-center">No invoices yet.</td></tr>'; return; }
    tb.innerHTML = rows.map((v) => {
      const paid = (v.status || "").toLowerCase() === "paid";
      const amt = "₹" + (Number(v.amount || 0) / 100).toLocaleString("en-IN", { minimumFractionDigits: 2 });
      const dl = v.pdf_url ? '<a href="' + esc(v.pdf_url) + '" target="_blank" rel="noopener" class="text-on-surface-variant hover:text-primary transition-colors" title="Download/View"><span class="material-symbols-outlined">download</span></a>' : '<span class="text-on-surface-variant/40"><span class="material-symbols-outlined">download</span></span>';
      return '<tr class="hover:bg-surface-container-highest transition-colors">' +
        '<td class="p-4 text-on-surface">' + esc(day(v.created_at)) + '</td>' +
        '<td class="p-4 text-on-surface">' + esc(amt) + '</td>' +
        '<td class="p-4"><span class="' + (paid ? "text-secondary" : "text-on-surface-variant") + ' flex items-center gap-1"><span class="material-symbols-outlined text-[16px]">' + (paid ? "check_circle" : "schedule") + '</span> ' + esc(v.status || "—") + '</span></td>' +
        '<td class="p-4 text-right">' + dl + '</td></tr>';
    }).join("");
  }

  onAction("blPortal", async (btn) => {
    const prev = btn.textContent; btn.disabled = true; btn.textContent = "Opening…";
    try {
      const r = await authFetch("/api/v1/billing/portal-link", { method: "POST" });
      const d = await r.json().catch(() => ({}));
      if (r.ok && d.portal_url) { window.open(d.portal_url, "_blank", "noopener"); }
      else { alert(d.detail || "Could not open the billing portal."); }
    } catch (_) { alert("Could not open the billing portal."); }
    finally { btn.disabled = false; btn.textContent = prev; }
  });

  loadUsage(); loadInvoices();
})();
