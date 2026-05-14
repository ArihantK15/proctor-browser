"""Admin status page — /api/v1/admin/status endpoint + /status-page HTML."""

import json
import os
import time
import logging

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from ..auth.admin_auth import require_admin
from ..limiter import limiter

_log = logging.getLogger("admin_status")

router = APIRouter(prefix="/api/v1/admin", tags=["admin_status"])

_STATUS_PAGE_CSS = """\
*{margin:0;padding:0;box-sizing:border-box}
body{font-family:-apple-system,BlinkMacSystemFont,sans-serif;background:#f5f7fa;color:#1e293b;padding:40px}
h1{font-size:24px;margin-bottom:8px}
.sub{color:#64748b;font-size:14px;margin-bottom:24px}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(280px,1fr));gap:16px}
.card{border-radius:12px;padding:20px;background:#fff;box-shadow:0 1px 3px rgba(0,0,0,.08)}
.card h3{font-size:14px;text-transform:uppercase;letter-spacing:.05em;color:#64748b;margin-bottom:12px}
.status-dot{display:inline-block;width:10px;height:10px;border-radius:50%;margin-right:8px}
.ok{background:#22c55e}
.warning{background:#f59e0b}
.critical,.error,.stale,.misconfigured,.no_heartbeat{background:#ef4444}
.unavailable{background:#94a3b8}
.noop{background:#a78bfa}
.value{font-size:28px;font-weight:600}
.meta{font-size:12px;color:#94a3b8;margin-top:4px}.meta span{margin-right:12px}
"""

_STATUS_PAGE_HTML = """\
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>System Status — Procta</title>
<style>{css}</style>
</head>
<body>
<h1>System Status</h1>
<p class="sub" id="ts">Loading...</p>
<div class="grid" id="grid"></div>
<script>
const TOKEN = localStorage.getItem('procta_token') || '';
const STATUS_URL = '/api/v1/admin/status';
const CHECK_ORDER = [
  ['supabase','Supabase Database'],
  ['redis','Redis Cache'],
  ['email','Email Service'],
  ['worker','Background Worker'],
  ['disk','Disk Space'],
  ['storage_write','Storage Write'],
  ['memory_pct','Memory'],
];

function cls(s){return (s||'').toLowerCase()}
async function load(){
  const ts=document.getElementById('ts');
  const grid=document.getElementById('grid');
  try{
    const r=await fetch(STATUS_URL,{headers:{Authorization:'Bearer '+TOKEN}});
    if(!r.ok){grid.innerHTML='<p style="color:red">Failed to load status (HTTP '+r.status+'). Check auth token.</p>';return}
    const d=await r.json();
    ts.textContent='Last updated: '+new Date().toLocaleString()+'  •  Uptime: '+d.uptime_sec+'s  •  '+d.health_checks+' checks';
    let html='';
    for(const [key,label] of CHECK_ORDER){
      const v=d.checks[key];
      if(v===undefined) continue;
      const s=cls(v);
      const dot=s=='ok'?'ok':s=='warning'?'warning':'critical';
      html+='<div class="card"><h3>'+label+'</h3><div><span class="status-dot '+dot+'"></span><span class="value">'+v+'</span></div></div>';
    }
    // remaining checks not in ORDER
    for(const k of Object.keys(d.checks)){
      if(CHECK_ORDER.findIndex(o=>o[0]===k)!==-1) continue;
      html+='<div class="card"><h3>'+k.replace(/_/g,' ')+'</h3><div><span class="value">'+JSON.stringify(d.checks[k])+'</span></div></div>';
    }
    grid.innerHTML=html;
  }catch(e){
    grid.innerHTML='<p style="color:red">Error: '+e.message+'</p>';
  }
}
load();
</script>
</body>
</html>
"""


@router.get("/status")
@limiter.limit("30/minute")
async def get_status(request: Request):
    """Return system status JSON (admin only)."""
    teacher = await require_admin(request)
    _ = teacher  # used for auth only

    checks = {}
    ok = True

    # Supabase
    try:
        from ..database import async_table as _atable
        await _atable("exam_config").select("id").limit(1).execute()
        checks["supabase"] = "ok"
    except Exception:
        checks["supabase"] = "error"
        ok = False

    # Redis
    try:
        from .. import cache as _cache
        if _cache:
            r = _cache._client() if hasattr(_cache, '_client') else None
            if r:
                r.ping()
                checks["redis"] = "ok"
            else:
                checks["redis"] = "unavailable"
        else:
            checks["redis"] = "unavailable"
    except Exception:
        checks["redis"] = "error"
        ok = False

    # Worker heartbeat
    try:
        from .. import cache as _cache
        if _cache:
            r = _cache._client() if hasattr(_cache, '_client') else None
            if r:
                hb = r.get("worker:last_heartbeat")
                if hb:
                    age = time.time() - float(hb)
                    checks["worker"] = "ok" if age < 60 else "stale"
                    if age >= 60:
                        ok = False
                else:
                    checks["worker"] = "no_heartbeat"
            else:
                checks["worker"] = "unavailable"
        else:
            checks["worker"] = "unavailable"
    except Exception:
        checks["worker"] = "error"

    # Email
    try:
        provider = os.environ.get("EMAIL_PROVIDER", "resend")
        email_from = os.environ.get("EMAIL_FROM", "")
        if provider != "noop" and email_from:
            checks["email"] = "ok"
        elif provider == "noop":
            checks["email"] = "noop"
        else:
            checks["email"] = "misconfigured"
            ok = False
    except Exception:
        checks["email"] = "error"

    # Memory
    try:
        import psutil
        mem = psutil.virtual_memory()
        pct = mem.percent
        checks["memory_pct"] = f"{pct}%"
    except ImportError:
        pass

    # Disk
    try:
        import shutil
        target = os.getenv("SCREENSHOTS_DIR", "/var/lib/proctor/screenshots")
        total, used, free = shutil.disk_usage(target)
        free_mb = free // (1024 * 1024)
        if free_mb < 500:
            checks["disk"] = "critical"
            ok = False
        elif free_mb < 2000:
            checks["disk"] = "warning"
        else:
            checks["disk"] = "ok"
        checks["disk_free_mb"] = f"{free_mb} MB"

        os.makedirs(target, exist_ok=True)
        test_path = os.path.join(target, ".health_write_test")
        with open(test_path, "wb") as f:
            f.write(b"ok")
        os.remove(test_path)
        checks["storage_write"] = "ok"
    except Exception:
        checks["storage_write"] = "error"

    uptime_sec = round(time.time() - _REQ_TS, 1) if "_REQ_TS" in dir() else 0

    return {
        "status": "ok" if ok else "degraded",
        "uptime_sec": uptime_sec,
        "health_checks": 0,
        "checks": checks,
    }


_REQ_TS = time.time()


@router.get("/status-page")
async def status_page(request: Request):
    """Admin status page HTML."""
    html = _STATUS_PAGE_HTML.replace("{css}", _STATUS_PAGE_CSS, 1)
    return HTMLResponse(content=html)
