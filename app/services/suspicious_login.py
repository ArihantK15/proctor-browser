"""Suspicious-login detection + email notification.

Fires AFTER a successful login. Doesn't block — purely a heads-up
to the user that someone (hopefully them) signed in from a new
location.

Detection logic:
  Look up the user's last 30 days of `login_success` events in the
  `auth_events` table (populated by services.auth_events.record).
  If the current request's IP /24 AND user-agent both differ from
  every prior login in that window → it's a "new device", send email.

Why /24 not exact IP: home/office IPs rotate within a /24 range
on most ISPs. Locking to exact IP would fire on every other login.
A /24 covers ~256 addresses — same ISP, same physical location 99%
of the time. UA must also differ so a browser update on the same
network doesn't trigger.

False-positive cost: a user gets a "new sign-in" email when they
genuinely log in from a coffee shop. Not great, but not terrible —
modern users expect this pattern from Google/Apple. Mitigated
further in a future iteration with a "this was me, trust this
device" link that whitelists the IP/24+UA pair for 30 days.

This service is FIRE-AND-FORGET. The login handler should call
`check_and_notify` in a background task — never block the response
on the email send.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

from ..database import async_table as _atable

logger = logging.getLogger(__name__)

_LOOKBACK_DAYS = 30


def _ip_to_subnet(ip: str) -> str:
    """Reduce an IP to its /24 prefix.

    `192.168.1.42` → `192.168.1`. IPv6 is collapsed to the first
    four hex groups (`/64`) which is the ISP's typical allocation.

    Returns an empty string for unparseable input — caller treats
    that as "no info, can't compare, don't notify".
    """
    if not ip:
        return ""
    if "." in ip:  # IPv4
        parts = ip.split(".")
        return ".".join(parts[:3]) if len(parts) == 4 else ""
    if ":" in ip:  # IPv6
        parts = ip.split(":")
        return ":".join(parts[:4])
    return ""


async def _recent_logins(user_kind: str, user_id: str) -> list[dict]:
    """Fetch successful logins for this user from the last 30 days."""
    cutoff = (datetime.now(timezone.utc) - timedelta(days=_LOOKBACK_DAYS)).isoformat()
    try:
        result = (await _atable("auth_events")
                  .select("ip,user_agent,created_at")
                  .eq("user_kind", user_kind)
                  .eq("user_id", user_id)
                  .eq("event_type", "login_success")
                  .gte("created_at", cutoff)
                  .limit(50)
                  .execute())
        return result.data or []
    except Exception as e:
        logger.warning("[suspicious_login] auth_events lookup failed: %s", e)
        return []


def _is_new_device(events: list[dict], cur_subnet: str, cur_ua: str) -> bool:
    """Determine if (subnet, ua) is unseen in the recent-logins set.

    We require BOTH to be different from every prior event. A user
    on the same WiFi who upgraded Chrome shouldn't get flagged; a
    user roaming to a new café shouldn't get flagged if their
    laptop's UA is unchanged. Only when both shift does it look
    like a different person on a different machine.

    The CURRENT login is the first row in `events` (we ran the
    record() call before this check). Skip the first match to
    avoid comparing the event against itself.
    """
    if not cur_subnet or not cur_ua:
        return False
    matched_self = False
    for ev in events:
        ev_subnet = _ip_to_subnet(ev.get("ip", ""))
        ev_ua = (ev.get("user_agent") or "").strip()
        if ev_subnet == cur_subnet and ev_ua == cur_ua and not matched_self:
            # This is the current login's own event row. Skip it once.
            matched_self = True
            continue
        if ev_subnet == cur_subnet or ev_ua == cur_ua:
            # Saw this subnet OR this UA recently → not new.
            return False
    return True  # No prior match — fresh device + fresh location


async def check_and_notify(
    *,
    user_kind: str,
    user_id: str,
    user_email: str,
    user_name: str,
    request_ip: str,
    user_agent: str,
) -> None:
    """Check whether this login looks suspicious; if so, email the user.

    Designed to run in a background task (asyncio.create_task) so the
    login response isn't blocked on the auth_events lookup + email
    send. Errors are swallowed — this is a heads-up, not a gate.
    """
    try:
        events = await _recent_logins(user_kind, user_id)
        if not events:
            # No history → can't meaningfully compare. Either the first
            # ever login (silent) or auth_events lookup failed (skip).
            return

        cur_subnet = _ip_to_subnet(request_ip)
        cur_ua = (user_agent or "").strip()
        if not _is_new_device(events, cur_subnet, cur_ua):
            return

        # Send the heads-up email. Import lazily so this module
        # doesn't pull the emailer into every test.
        from ..emailer import send_suspicious_login_email
        send_suspicious_login_email(
            to_email=user_email,
            to_name=user_name or user_email.split("@", 1)[0],
            ip=request_ip,
            user_agent=user_agent,
            when=datetime.now(timezone.utc),
        )
    except Exception as e:
        # Never raise — a failed suspicious-login email shouldn't
        # affect the user's actual login flow.
        logger.warning("[suspicious_login] check failed for %s: %s", user_email, e)
