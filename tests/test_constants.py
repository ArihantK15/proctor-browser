"""app/constants.py runs real validation/derivation logic at IMPORT TIME
(env-var requirement checks, SystemExit/raise on misconfig, JWT key-ring
derivation) — none of it was covered by any test before this file, despite
57 dependents across the codebase (the highest fan-in of any file in the
repo per repowise). Import-time side effects can't be safely re-triggered
in-process (they call sys.exit / raise on misconfig, and Python caches
imports), so these run each case in a subprocess with a controlled env.
"""
import ast
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

BASE_ENV = {
    "SUPABASE_JWT_SECRET": "test-secret-key-at-least-32-chars-long!!",
}


def _run(extra_env: dict, code: str) -> subprocess.CompletedProcess:
    """Run `code` in a fresh subprocess with BASE_ENV + extra_env."""
    env = {**os.environ, **BASE_ENV, **extra_env}
    return subprocess.run(
        [sys.executable, "-c", code],
        cwd=ROOT, env=env, capture_output=True, text=True, timeout=30,
    )


class TestRequiredEnv:
    def test_missing_required_env_exits_fatal(self):
        env = dict(os.environ)
        env.pop("SUPABASE_JWT_SECRET", None)
        r = subprocess.run(
            [sys.executable, "-c", "import app.constants"],
            cwd=ROOT, env=env, capture_output=True, text=True, timeout=30,
        )
        assert r.returncode == 1
        assert "FATAL" in r.stderr
        assert "SUPABASE_JWT_SECRET" in r.stderr


class TestSecretKeyLengthGate:
    def test_short_secret_raises_in_production(self):
        r = _run(
            {"SUPABASE_JWT_SECRET": "short", "ENV": "production"},
            "import app.constants",
        )
        assert r.returncode != 0
        assert "SUPABASE_JWT_SECRET is only" in r.stderr

    def test_short_secret_warns_but_survives_in_dev(self):
        r = _run(
            {"SUPABASE_JWT_SECRET": "short", "ENV": "development"},
            "import app.constants; print('IMPORTED_OK')",
        )
        assert r.returncode == 0, r.stderr
        assert "IMPORTED_OK" in r.stdout

    def test_default_env_is_treated_as_production(self):
        # constants.py treats an EMPTY/unset ENV the same as "production"
        # (fail-closed default) — a short key with no ENV set must still raise.
        r = _run({"SUPABASE_JWT_SECRET": "short"}, "import app.constants")
        assert r.returncode != 0
        assert "SUPABASE_JWT_SECRET is only" in r.stderr


class TestJwtKeyRing:
    def test_explicit_signing_key_wins(self):
        r = _run(
            {"JWT_ADMIN_SIGNING_KEY": "explicit-admin-key"},
            "import app.constants as c; print(c.ADMIN_SIGNING_KEY)",
        )
        assert r.returncode == 0, r.stderr
        assert r.stdout.strip() == "explicit-admin-key"

    def test_no_explicit_key_dev_falls_back_to_derived_key(self):
        # Dev default: JWT_ACCEPT_DERIVED_LEGACY_KEYS defaults to true when
        # ENV isn't production, so an unset purpose-specific key still works.
        r = _run(
            {"ENV": "development"},
            "import app.constants as c; print(bool(c.ADMIN_SIGNING_KEY))",
        )
        assert r.returncode == 0, r.stderr
        assert r.stdout.strip() == "True"

    def test_no_explicit_key_prod_with_legacy_disabled_raises(self):
        r = _run(
            {"ENV": "production", "JWT_ACCEPT_DERIVED_LEGACY_KEYS": "false"},
            "import app.constants",
        )
        assert r.returncode != 0
        assert "has no explicit" in r.stderr

    def test_previous_keys_appended_for_rotation(self):
        r = _run(
            {
                "JWT_ADMIN_SIGNING_KEY": "new-key",
                "JWT_ADMIN_SIGNING_KEY_PREVIOUS": "old-key-1,old-key-2",
            },
            "import app.constants as c; print(c.ADMIN_SIGNING_KEYS)",
        )
        assert r.returncode == 0, r.stderr
        keys = ast.literal_eval(r.stdout.strip())
        assert keys[0] == "new-key"
        assert "old-key-1" in keys
        assert "old-key-2" in keys


class TestInviteCapGuard:
    def test_positive_cap_respected(self):
        r = _run(
            {"INVITE_DAILY_CAP": "123"},
            "import app.constants as c; print(c.INVITE_DAILY_CAP)",
        )
        assert r.returncode == 0, r.stderr
        assert r.stdout.strip() == "123"

    def test_zero_cap_clamped_to_default(self):
        # A misconfigured 0 (thinking it means "unlimited") must not silently
        # brick every invite send for 24h — clamp back to the 5000 default.
        r = _run(
            {"INVITE_DAILY_CAP": "0"},
            "import app.constants as c; print(c.INVITE_DAILY_CAP)",
        )
        assert r.returncode == 0, r.stderr
        assert r.stdout.strip() == "5000"

    def test_negative_cap_clamped_to_default(self):
        r = _run(
            {"INVITE_DAILY_CAP": "-5"},
            "import app.constants as c; print(c.INVITE_DAILY_CAP)",
        )
        assert r.returncode == 0, r.stderr
        assert r.stdout.strip() == "5000"


class TestCorsOrigins:
    def test_electron_origins_always_present_with_default_origins(self):
        r = _run({}, "import app.constants as c; print(c.CORS_ALLOWED_ORIGINS)")
        assert r.returncode == 0, r.stderr
        origins = ast.literal_eval(r.stdout.strip())
        assert "procta-lobby://lobby" in origins
        assert "procta-lobby://exam" in origins

    def test_electron_origins_survive_custom_cors_env(self):
        # A custom CORS_ALLOWED_ORIGINS must EXTEND, not replace, the
        # Electron window origins — otherwise packaged desktop builds can
        # log in from a restored cookie but fail every API preflight after.
        r = _run(
            {"CORS_ALLOWED_ORIGINS": "https://example.com"},
            "import app.constants as c; print(c.CORS_ALLOWED_ORIGINS)",
        )
        assert r.returncode == 0, r.stderr
        origins = ast.literal_eval(r.stdout.strip())
        assert "https://example.com" in origins
        assert "procta-lobby://lobby" in origins
        assert "procta-lobby://exam" in origins

    def test_null_origin_never_present(self):
        r = _run({}, "import app.constants as c; print(c.CORS_ALLOWED_ORIGINS)")
        assert r.returncode == 0, r.stderr
        origins = ast.literal_eval(r.stdout.strip())
        assert "null" not in origins


class TestPlans:
    def test_every_paid_plan_has_a_positive_overage_price(self):
        import app.constants as c
        for key, plan in c.PLANS.items():
            if key == "enterprise":
                continue
            assert plan["overage_price_inr"] > 0, f"{key} has no overage price"

    def test_plan_student_limits_strictly_increase(self):
        import app.constants as c
        order = ["starter", "growth", "pro", "enterprise"]
        limits = [c.PLANS[k]["students"] for k in order]
        assert limits == sorted(limits)
