#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = ["httpx[socks]"]
# ///
# Version: 0.1.1
"""
ccpace - pace your Claude quota. Multi-account usage monitor for Claude
subscriptions: real utilization from the official usage endpoint, a
countable 5h-window budget, and forecasts from your own history.

READ-ONLY against the API, with one deliberate exception: when a token
is expired it is refreshed via the official OAuth refresh flow and the
new token is written back to the credentials file (same as Claude Code
itself does). Nothing else is ever sent or modified.

examples:
  %(prog)s                       one glance, all discovered accounts
  %(prog)s --watch               live TUI (r=refresh q=quit)
  %(prog)s --raw                 raw usage JSON, all accounts
  %(prog)s -f ~/.claude/.credentials*.json --watch
  %(prog)s --watch --threshold 90 --interval 300
  %(prog)s --watch --ntfy https://ntfy.sh/mytopic
  %(prog)s --watch --bark https://api.day.app/YOURKEY
  CCPACE_TZ=America/New_York,Asia/Tokyo %(prog)s

usage bars merge usage and window-elapsed time:
  █ both passed   ▓ usage ahead of time (hot)
  ▒ time ahead of usage (headroom)   ░ untouched

on terminals >= 72 cols the 7d row grows a window ledger: one cell
per 5h window of the period (34 cells), left to right in time —
  ▁▂▃▄▅▆▇█  a window that ran; height = 7d points it burned
  ·         ran, burned under a point
  ░         unknown: no samples on record for that window
  ▮         the window you are in now
  ▫         a window still ahead of you
  ×         a window the 7d pool will not cover at the current pace
  ┤         access ends here (trial end, or the derived sub period end
            assumed binding — the API states no renewal/cancel date)
count ▮ plus what follows it for the advisor's "windows left". History
comes from the sample store (shared with claude-code-statusline; see
docs/data.md); with no store the past is honestly ░, not empty.

notifications: system notify is automatic; add channels with flags/env.
  --ntfy URL / CCPACE_NTFY       ntfy topic URL
  --bark URL / CCPACE_BARK       bark endpoint (https://api.day.app/KEY)
  --notifier PATH / CCPACE_NOTIFIER  custom script, JSON on stdin:
    {"event": "threshold|full|delta|pace|reset", "account": "...", "data": {...}}
"""

from __future__ import annotations

import argparse
import asyncio
import glob as globlib
import json
import logging
import os
import platform
import random
import select
import shutil
import signal
import subprocess
import sys
import termios
import tty
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Literal

import httpx

__version__ = "0.1.1"
CLI_VERSION = "2.1.220"
PROG = "ccpace"
LOGGER = logging.getLogger(PROG)

EXIT_OK = 0
EXIT_RUNTIME = 1
EXIT_USAGE = 2
EXIT_CONFIG = 5
EXIT_INTERRUPT = 130

OAUTH_BETA = "oauth-2025-04-20"
USER_AGENT_CLI = f"claude-cli/{CLI_VERSION} (external, cli)"
# the CLI's oauth client (token exchange, profile) is plain axios
USER_AGENT_AXIOS = "axios/1.15.2"


DEFAULT_WATCH_INTERVAL = 60 * 15
MIN_WATCH_INTERVAL = 60
WATCH_JITTER_FRAC = 0.10
DEFAULT_WATCH_THRESHOLD = 80
WAIT_MODE_CHECK_INTERVAL = 60
# on fetch errors: honor Retry-After, else exponential backoff in this range
BACKOFF_BASE_SEC = 60
BACKOFF_MAX_SEC = 900
PROFILE_TTL_SEC = 24 * 3600
CREDITS_TTL_SEC = 5 * 60
FORECAST_REBUILD_SEC = 3600
FORECAST_MIN_DAYS = 3
FORECAST_HALF_LIFE_DAYS = 14.0
USAGE_LOG_MAX_BYTES = int(os.getenv("USAGE_LOG_MAX_BYTES", str(32 * 1024 * 1024)))

WINDOW_5H_SEC = 5 * 3600
WINDOW_7D_SEC = 7 * 86400
# pace = usage%/elapsed%; ratios are noise until the window has aged a bit
PACE_MIN_ELAPSED_FRAC = 0.05
PACE_WARN_RATIO = 1.15
PACE_WARN_MIN_UTIL = 10

# 7d window-budget strip: one cell per 5h window (7d = 33.6 -> 34 cells,
# the last a 3h stub); cells right of │ are countable as windows left
WEEK_STRIP_WIDTH = 34
WEEK_STRIP_MIN_COLS = 72
# bar column offset: tag(5) + space + util(3) + "% " = 11
ROW_BAR_INDENT = 11

HTTP_LIMITS = httpx.Limits(max_connections=20, max_keepalive_connections=10)
HTTP_TIMEOUT = httpx.Timeout(30.0, connect=10.0)

MACOS_ALERT_SOUND = "Submarine"
RESET = "\033[0m"
BOLD = "\033[1m"
DIM = "\033[2m"
RED = "\033[31m"
GREEN = "\033[32m"
YELLOW = "\033[33m"
BLUE = "\033[34m"
MAGENTA = "\033[35m"
CYAN = "\033[36m"

# --- utilities ---

def supports_color() -> bool:
    return hasattr(sys.stdout, "isatty") and sys.stdout.isatty()


def supports_unicode() -> bool:
    if not sys.stdout.isatty():
        return False
    encoding = sys.stdout.encoding or ""
    return "utf" in encoding.lower()


def check_keypress(timeout: float = 0) -> str | None:
    """Check for keypress without blocking. Returns key or None."""
    if not sys.stdin.isatty():
        return None

    old_settings = termios.tcgetattr(sys.stdin)
    try:
        tty.setcbreak(sys.stdin.fileno())
        if select.select([sys.stdin], [], [], timeout)[0]:
            return sys.stdin.read(1)
    finally:
        termios.tcsetattr(sys.stdin, termios.TCSADRAIN, old_settings)
    return None


def get_os() -> Literal["darwin", "linux"] | str:
    return platform.system().lower()


def parse_display_timezones() -> list[tuple[Any, str]]:
    """Parse CCPACE_TZ (or CLAUDE_TZ) env var. Returns [(tz_obj, label), ...]. Max 4 zones."""
    tz_spec = (os.getenv("CCPACE_TZ") or os.getenv("CLAUDE_TZ") or "").strip()
    if not tz_spec:
        return [(None, "local")]

    result = []
    for name in tz_spec.split(",")[:4]:
        name = name.strip()
        if not name:
            continue
        try:
            from zoneinfo import ZoneInfo

            parts = name.split("/")
            if len(parts) == 1:
                short_label = name[:6]
            else:
                city = parts[-1]
                CITY_ABBREVS = {
                    "New_York": "NY",
                    "Los_Angeles": "LA",
                    "Chicago": "CHI",
                    "Denver": "DEN",
                    "Phoenix": "PHX",
                    "Shanghai": "SHA",
                    "Hong_Kong": "HK",
                    "Singapore": "SG",
                }
                short_label = CITY_ABBREVS.get(city, city.replace("_", "")[:6])
            result.append((ZoneInfo(name), short_label))
        except ImportError:
            LOGGER.warning("zoneinfo not available, ignoring CCPACE_TZ")
            return [(None, "local")]
        except Exception as e:
            LOGGER.warning("invalid timezone %s: %s", name, e)

    return result if result else [(None, "local")]


DISPLAY_TZS = parse_display_timezones()


def format_multi_tz(dt: datetime, show_all: bool = True) -> str:
    """Format datetime with multi-TZ support.

    Multi-TZ format: "03/15 08:30 NY | Tokyo: 22:30+1 | UTC: 13:30"
    """
    if len(DISPLAY_TZS) == 1 or not show_all:
        tz, _ = DISPLAY_TZS[0]
        return dt.astimezone(tz).strftime("%m/%d %H:%M")

    primary_tz, primary_label = DISPLAY_TZS[0]
    primary_time = dt.astimezone(primary_tz)
    result = f"{primary_time.strftime('%m/%d %H:%M')} {primary_label}"

    for tz, label in DISPLAY_TZS[1:]:
        t = dt.astimezone(tz)
        day_diff = (t.date() - primary_time.date()).days
        suffix = (
            f"+{day_diff}" if day_diff > 0 else (f"{day_diff}" if day_diff < 0 else "")
        )
        result += f" | {label}: {t.strftime('%H:%M')}{suffix}"

    return result


def setup_logging(quiet: bool, verbose: int) -> None:
    level = logging.WARNING if quiet else logging.INFO
    if verbose >= 1:
        level = logging.DEBUG
    logging.basicConfig(
        level=level,
        format=(
            "%(levelname)s %(message)s"
            if not verbose
            else "%(levelname)s [%(name)s] %(message)s"
        ),
    )
    if verbose == 0:
        logging.getLogger("httpx").setLevel(logging.WARNING)
    elif verbose >= 2:
        logging.getLogger("httpx").setLevel(logging.DEBUG)


def log_http(label: str, data: dict, trace: bool = False) -> None:
    """Log HTTP request/response when trace enabled."""
    if not trace:
        return
    LOGGER.debug("%s: %s", label, json.dumps(data))

# --- credentials ---

def get_token_from_keychain() -> str | None:
    """Extract OAuth token from macOS Keychain (service: Claude Code-credentials)."""
    if get_os() != "darwin":
        return None

    try:
        import getpass
        user = getpass.getuser()
        service = "Claude Code-credentials"

        # security find-generic-password -a $USER -s "Claude Code-credentials" -w
        result = subprocess.run(
            ["security", "find-generic-password", "-a", user, "-s", service, "-w"],
            capture_output=True,
            text=True,
            check=False,
        )

        if result.returncode != 0:
            LOGGER.debug("keychain lookup failed: %s", result.stderr.strip())
            return None

        kc_data = result.stdout.strip()
        if not kc_data:
            return None

        # Try parsing as JSON first (full credentials object)
        try:
            data = json.loads(kc_data)
            token = data.get("claudeAiOauth", {}).get("accessToken") or data.get("access_token")
            if token:
                LOGGER.debug("token loaded from keychain JSON (%d chars)", len(token))
                return token
        except json.JSONDecodeError:
            # Not JSON - might be plain token string
            if kc_data.startswith("sk-ant-"):
                LOGGER.debug("token loaded from keychain plain text (%d chars)", len(kc_data))
                return kc_data
            LOGGER.debug("keychain data format unknown (not JSON, not token)")
            return None

    except (OSError, subprocess.SubprocessError) as e:
        LOGGER.debug("keychain access failed: %s", e)
        return None


def discover_credential_files() -> list[Path]:
    """Find all credential files in ~/.claude/."""
    claude_dir = Path.home() / ".claude"
    if not claude_dir.exists():
        return []

    files = sorted(claude_dir.glob(".credentials*.json"))
    return files


def get_token_from_file(cred_file: Path) -> str | None:
    """Extract OAuth token from credentials file."""
    if not cred_file.exists():
        LOGGER.debug("credential file not found: %s", cred_file)
        return None

    try:
        data = json.loads(cred_file.read_text())
        token = data.get("claudeAiOauth", {}).get("accessToken")
        if token:
            LOGGER.debug("token loaded from %s", cred_file)
            return token
    except (json.JSONDecodeError, OSError) as e:
        LOGGER.debug("failed to parse %s: %s", cred_file, e)

    return None


def get_all_credentials(
    cred_patterns: list[Path] | None, explicit_file: bool
) -> list[tuple[Path, str]]:
    """Get all available credentials as (path, token) pairs."""
    if explicit_file and cred_patterns:
        cred_files = []
        for pattern in cred_patterns:
            pattern_str = str(pattern)
            if "*" in pattern_str or "?" in pattern_str or "[" in pattern_str:
                matches = sorted(Path(p) for p in globlib.glob(pattern_str))
                if not matches:
                    LOGGER.warning("no files match pattern: %s", pattern)
                cred_files.extend(matches)
            else:
                cred_files.append(pattern)

        if not cred_files:
            raise SystemExit(f"{PROG}: error: no credential files found")

        results = []
        for cf in cred_files:
            refreshed_token = refresh_token_if_needed(cf)

            token = refreshed_token or get_token_from_file(cf)
            if token:
                results.append((cf, token))
            else:
                LOGGER.warning("no token in %s", cf)

        if not results:
            raise SystemExit(f"{PROG}: error: no valid tokens found")

        return results

    env_token = os.getenv("CLAUDE_CODE_OAUTH_TOKEN")
    if env_token:
        return [(Path("env:CLAUDE_CODE_OAUTH_TOKEN"), env_token)]

    # Try credential files first (preferred - can be auto-refreshed)
    cred_files = discover_credential_files()

    results = []
    for cf in cred_files:
        refreshed_token = refresh_token_if_needed(cf)

        token = refreshed_token or get_token_from_file(cf)
        if token:
            results.append((cf, token))

    if results:
        return results

    # Fallback to macOS Keychain (cannot be auto-refreshed)
    keychain_token = get_token_from_keychain()
    if keychain_token:
        LOGGER.warning("using keychain token (cannot auto-refresh, may be expired)")
        return [(Path("keychain:Claude Code-credentials"), keychain_token)]

    raise SystemExit(
        f"{PROG}: error: no OAuth token found\n"
        f"Log in with Claude Code first (claude login), or set CLAUDE_CODE_OAUTH_TOKEN"
    )


# --- token refresh ---

OAUTH_TOKEN_URL = "https://platform.claude.com/v1/oauth/token"
OAUTH_CLIENT_ID = "9d1c250a-e61b-44d9-88ed-5944d1962f5e"
OAUTH_REFRESH_SCOPES = "user:profile user:inference user:sessions:claude_code user:mcp_servers"
REFRESH_THRESHOLD_SECONDS = 5 * 60


def is_token_expired_or_soon(
    expires_at_ms: int | None, threshold_sec: int = REFRESH_THRESHOLD_SECONDS
) -> bool:
    """Check if token is expired or will expire soon."""
    if not expires_at_ms:
        return False

    expires_at = datetime.fromtimestamp(expires_at_ms / 1000, tz=timezone.utc)
    now = datetime.now(timezone.utc)
    remaining = (expires_at - now).total_seconds()

    return remaining < threshold_sec


def refresh_oauth_token(refresh_token: str) -> dict[str, Any] | None:
    """Refresh OAuth token using refresh_token grant."""
    try:
        resp = httpx.post(
            OAUTH_TOKEN_URL,
            json={
                "grant_type": "refresh_token",
                "refresh_token": refresh_token,
                "client_id": OAUTH_CLIENT_ID,
                "scope": OAUTH_REFRESH_SCOPES,
            },
            timeout=30,
        )
        resp.raise_for_status()
        data = resp.json()

        LOGGER.debug("token refreshed")
        return {
            "access_token": data.get("access_token"),
            "refresh_token": data.get("refresh_token"),
            "expires_in": data.get("expires_in"),
        }
    except httpx.HTTPError as e:
        LOGGER.warning("token refresh failed: %s", e)
        return None


def update_credentials_file(
    cred_file: Path,
    new_access_token: str,
    new_refresh_token: str | None,
    expires_in: int | None,
) -> bool:
    """Update credentials file with new tokens."""
    if not cred_file.exists():
        LOGGER.debug("credentials file not found: %s", cred_file)
        return False

    try:
        data = json.loads(cred_file.read_text())

        if "claudeAiOauth" not in data:
            LOGGER.debug("no claudeAiOauth in %s", cred_file)
            return False

        data["claudeAiOauth"]["accessToken"] = new_access_token

        if new_refresh_token:
            data["claudeAiOauth"]["refreshToken"] = new_refresh_token

        if expires_in:
            now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
            data["claudeAiOauth"]["expiresAt"] = now_ms + (expires_in * 1000)

        cred_file.write_text(json.dumps(data, indent=2))
        LOGGER.info("credentials updated: %s", cred_file)
        return True

    except (json.JSONDecodeError, OSError) as e:
        LOGGER.warning("failed to update %s: %s", cred_file, e)
        return False


def refresh_token_if_needed(cred_file: Path, force: bool = False) -> str | None:
    """Check and refresh token if expired or expiring soon. Returns new token or None."""
    if not cred_file.exists():
        return None

    try:
        data = json.loads(cred_file.read_text())
        oauth = data.get("claudeAiOauth", {})

        expires_at = oauth.get("expiresAt")
        refresh_token = oauth.get("refreshToken")

        if not refresh_token:
            LOGGER.debug("no refresh token available")
            return None

        if not force and not is_token_expired_or_soon(expires_at):
            return None

        LOGGER.info("refreshing token...")

        refresh_result = refresh_oauth_token(refresh_token)
        if not refresh_result:
            return None

        new_access = refresh_result.get("access_token")
        new_refresh = refresh_result.get("refresh_token")
        expires_in = refresh_result.get("expires_in")

        if not new_access:
            LOGGER.warning("no access_token in refresh response")
            return None

        if update_credentials_file(cred_file, new_access, new_refresh, expires_in):
            return new_access

        return None

    except (json.JSONDecodeError, OSError) as e:
        LOGGER.debug("failed to check token expiry: %s", e)
        return None

# --- info methods ---

def oauth_headers(token: str) -> dict[str, str]:
    """Headers for the claude-cli client path (usage, client_data, settings).

    Pinned from the 2026-07-21 trace; endpoints not re-observed in the
    2026-08-04 capture keep this shape.
    """
    return {
        "Accept": "application/json, text/plain, */*",
        "anthropic-beta": OAUTH_BETA,
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "User-Agent": USER_AGENT_CLI,
    }


def axios_headers(
    token: str, content_type: bool = False, no_cache: bool = False
) -> dict[str, str]:
    """Headers for the CLI's axios oauth client (profile, roles): no beta.

    As traced from claude-cli 2.1.220: profile sends
    content-type + cache-control, roles sends neither.
    """
    headers = {
        "Accept": "application/json, text/plain, */*",
        "Authorization": f"Bearer {token}",
        "User-Agent": USER_AGENT_AXIOS,
    }
    if no_cache:
        headers["Cache-Control"] = "no-cache"
    if content_type:
        headers["Content-Type"] = "application/json"
    return headers


def info_hello() -> int:
    """Health check (no auth required): both hellos the CLI sends at startup."""
    urls = [
        "https://platform.claude.com/v1/oauth/hello",
        "https://api.anthropic.com/api/hello",
    ]
    headers = {
        "Accept": "application/json, text/plain, */*",
        "User-Agent": USER_AGENT_CLI,
    }
    results = {}
    exit_code = EXIT_OK
    for url in urls:
        try:
            resp = httpx.get(url, headers=headers, timeout=30)
            resp.raise_for_status()
            results[url] = resp.json()
        except httpx.HTTPError as e:
            LOGGER.error("health check failed: %s: %s", url, e)
            exit_code = EXIT_RUNTIME
    if results:
        print(json.dumps(results, indent=2))
    return exit_code


def info_profile(credentials: list[tuple[Path, str]]) -> int:
    """Show user profile for all accounts."""
    results = {} if len(credentials) > 1 else None
    exit_code = EXIT_OK

    for cred_path, token in credentials:
        try:
            resp = httpx.get(
                "https://api.anthropic.com/api/oauth/profile",
                headers=axios_headers(token, content_type=True, no_cache=True),
                timeout=30,
            )
            resp.raise_for_status()
            data = resp.json()

            if results is not None:
                results[str(cred_path)] = data
            else:
                print(json.dumps(data, indent=2))

        except httpx.HTTPStatusError as e:
            label = str(cred_path)
            status = e.response.status_code
            LOGGER.error("%s: request failed (%d)", label, status)
            try:
                LOGGER.error("%s: %s", label, e.response.text)
            except Exception:
                pass
            exit_code = EXIT_RUNTIME
        except httpx.HTTPError as e:
            LOGGER.error("%s: %s: %s", str(cred_path), type(e).__name__, e)
            exit_code = EXIT_RUNTIME

    if results:
        print(json.dumps(results, indent=2))

    return exit_code

# --- usage monitoring ---

def format_bar(percent: int, width: int = 10, color: bool = True) -> str:
    """Format block progress bar."""
    if percent > 0:
        filled = min(width, max(1, percent * width // 100))
    else:
        filled = 0
    empty = width - filled

    if not color or not supports_color():
        return "█" * filled + "░" * empty

    if percent >= 80:
        color_code = RED
    elif percent >= 60:
        color_code = YELLOW
    else:
        color_code = GREEN

    return f"{color_code}{'█' * filled}{DIM}{'░' * empty}{RESET}"


def format_dual_bar(
    util: int,
    time_pct: int | None,
    pace: float | None = None,
    width: int = 10,
    color: bool = True,
) -> str:
    """Usage and window-elapsed merged into one bar.

    █ both passed | ▓ usage ahead of time (hot) | ▒ time ahead of usage
    (headroom) | ░ untouched. The seam marks the smaller of the two.
    """
    if time_pct is None:
        return format_bar(util, width, color)

    cu = min(width, max(1, util * width // 100)) if util > 0 else 0
    ct = min(width, max(1, time_pct * width // 100)) if time_pct > 0 else 0
    lo, hi = min(cu, ct), max(cu, ct)
    usage_leads = cu > ct
    body = "█" * lo
    gap = ("▓" if usage_leads else "▒") * (hi - lo)
    rest = "░" * (width - hi)

    if not color or not supports_color():
        return body + gap + rest

    if util >= 80:
        fill_color = RED
    elif util >= 60:
        fill_color = YELLOW
    else:
        fill_color = GREEN
    if usage_leads:
        gap_color = RED if (pace or 0) >= 1.5 else YELLOW
    else:
        gap_color = DIM
    return f"{fill_color}{body}{gap_color}{gap}{DIM}{rest}{RESET}"


def windows_left(entry: dict) -> int:
    """Whole 5h windows still touchable in the window, current one included."""
    return max(0, int((entry["remaining"] + WINDOW_5H_SEC - 1) // WINDOW_5H_SEC))


def data_root() -> Path:
    """Shared store root (docs/data.md): statusline's home, by design.

    Same machine, same account, one history — ccpace and
    claude-code-statusline read and write the same record shape here.
    CCPACE_DATA_DIR overrides for isolation (tests, exotic setups).
    """
    if env := os.getenv("CCPACE_DATA_DIR"):
        return Path(env).expanduser()
    config_dir = os.getenv("CLAUDE_CONFIG_DIR")
    home = Path(config_dir).expanduser() if config_dir else Path.home() / ".claude"
    return home / "statusline"


def account_dir(alias: str) -> Path:
    """Where this account's samples and caches live (write target)."""
    return data_root() / "accounts" / alias


def usage_store_paths(alias: str, log_dir: Path | None = None) -> list[Path]:
    """Candidate JSONL sample stores, richest first.

    Reads every place history may live: the shared store keyed by alias,
    statusline's deva-tag variant (auth-file-<alias>), and the unscoped
    single-account layout. Without a store there is no per-window
    history to draw, only unknowns.
    """
    root = data_root()
    paths = []
    if log_dir:
        paths.append(log_dir / "usage.jsonl")
    for scope in (f"accounts/{alias}", f"accounts/auth-file-{alias}", ""):
        base = root / scope if scope else root
        paths.extend([base / "usage.jsonl.1", base / "usage.jsonl"])
    return [p for p in paths if p.is_file()]


def load_usage_samples(
    paths: list[Path],
) -> list[tuple[float, float, float, str]]:
    """Parse sample stores into (sample_ts, five_hour_reset_ts, seven_util, uuid).

    Accepts both the store-v1/statusline shape ({"timestamp","five_hour",
    "seven_day","user":{...}}) and claudex's legacy {"ts","usage":{...}}
    rows; anything else is skipped rather than guessed at. uuid is ""
    when the row does not carry one — readers that aggregate MUST
    partition by uuid (docs/data.md): unscoped stores interleave
    accounts, and mixing them produces garbage burn rates.
    """
    out: list[tuple[float, float, float, str]] = []
    for path in paths:
        try:
            with path.open(encoding="utf-8") as f:
                for line in f:
                    try:
                        row = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    body = row.get("usage") if isinstance(row.get("usage"), dict) else row
                    five = body.get("five_hour") or {}
                    seven = body.get("seven_day") or {}
                    reset = parse_reset_dt(five.get("resets_at"))
                    util = seven.get("utilization")
                    if not reset or util is None:
                        continue
                    ts = row.get("timestamp")
                    if ts is None:
                        stamp = parse_reset_dt(row.get("ts"))
                        ts = stamp.timestamp() if stamp else None
                    if ts is None:
                        continue
                    uuid = (row.get("user") or {}).get("uuid") or (
                        (row.get("account") or {}).get("account") or {}
                    ).get("uuid") or ""
                    out.append((float(ts), reset.timestamp(), float(util), uuid))
        except OSError:
            continue
    return out


def build_window_history(
    samples: list[tuple[float, float, float]], period_start: float, width: int
) -> tuple[dict[int, float], tuple[float, float] | None]:
    """Attribute observed 7d burn to fixed 5h slots of the current period.

    Slot i spans [period_start + i*5h, +5h). A 5h window instance is keyed
    by its reset time rounded to 5min (the API jitters it by microseconds,
    and 05:59:59/06:00:00 are one window, not two). Cost is the 7d
    utilization delta observed inside that window. Returns (costs, span),
    where span is the store's own coverage — slots inside it with no
    samples are genuinely idle, slots outside it are unknown.
    """
    if not samples:
        return {}, None
    span = (min(s[0] for s in samples), max(s[0] for s in samples))

    by_window: dict[float, list[float]] = {}
    for _ts, reset_ts, util, _uuid in samples:
        key = round(reset_ts / 300) * 300
        by_window.setdefault(key, []).append(util)

    costs: dict[int, float] = {}
    for key, utils in by_window.items():
        start = key - WINDOW_5H_SEC
        slot = int((start - period_start) // WINDOW_5H_SEC)
        if 0 <= slot < width:
            costs[slot] = costs.get(slot, 0.0) + (max(utils) - min(utils))
    return costs, span


# --- forecast ---

def weekday_burn_forecast(
    samples: list[tuple[float, float, float, str]],
    account_uuid: str,
    now: datetime,
) -> dict | None:
    """Learn the account's weekday burn signature from the sample store.

    Daily burn = sum of positive deltas of 7d utilization within a local
    calendar day (negative deltas are window resets; ignored by
    construction). Weekdays are EWMA-weighted with a 14-day half-life so
    plan changes fade instead of poisoning the profile. Same numbers as
    statusline's forecast.cache — two surfaces, one model.

    Rows carrying a different account uuid are excluded; unlabeled rows
    (legacy stores) are kept because alias-scoped dirs are single-account
    by construction.
    """
    tz = primary_tz() or datetime.now().astimezone().tzinfo
    rows = sorted(
        (s for s in samples if not account_uuid or not s[3] or s[3] == account_uuid),
        key=lambda s: s[0],
    )
    if not rows:
        return None

    daily: dict[int, float] = {}  # local day ordinal -> burn %
    recent_24h = recent_48h = 0.0
    now_ts = now.timestamp()
    prev: tuple[float, float] | None = None
    for ts, _reset, util, _u in rows:
        if prev is not None:
            delta = util - prev[1]
            if delta > 0:
                day = datetime.fromtimestamp(ts, tz).date().toordinal()
                daily[day] = daily.get(day, 0.0) + delta
                if now_ts - ts <= 86400:
                    recent_24h += delta
                if now_ts - ts <= 2 * 86400:
                    recent_48h += delta
        prev = (ts, util)

    if not daily:
        return None

    today = datetime.fromtimestamp(now_ts, tz).date().toordinal()
    weights: dict[int, float] = {}
    burns: dict[int, float] = {}
    for day, burn in daily.items():
        if day >= today:
            continue  # today is partial; never a training day
        dow = day % 7  # date.toordinal(): day 1 = Mon, so %7 -> 0=Sun..6=Sat
        w = 0.5 ** ((today - day) / FORECAST_HALF_LIFE_DAYS)
        weights[dow] = weights.get(dow, 0.0) + w
        burns[dow] = burns.get(dow, 0.0) + w * burn

    profile = {
        str(d): round(burns[d] / weights[d], 2) if weights.get(d) else -1
        for d in range(7)
    }
    return {
        "computed_at": int(now_ts),
        "days_history": sum(1 for d in daily if d < today),
        "recent_24h": round(recent_24h, 2),
        "recent_48h": round(recent_48h, 2),
        "weekday_profile": profile,
    }


def write_forecast_cache(alias: str, forecast: dict) -> None:
    """Persist the profile (rebuild at most hourly; disposable, derived)."""
    path = account_dir(alias) / "forecast.cache"
    try:
        if path.is_file():
            prev = json.loads(path.read_text())
            if forecast["computed_at"] - prev.get("computed_at", 0) < FORECAST_REBUILD_SEC:
                return
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(f".tmp.{os.getpid()}")
        tmp.write_text(json.dumps(forecast, separators=(",", ":")))
        tmp.replace(path)
    except (OSError, json.JSONDecodeError, ValueError):
        pass


def forecast_line(
    forecast: dict | None,
    entry: dict,
    now: datetime,
    access_end: datetime | None = None,
) -> str | None:
    """Project the rest of the 7d window on the weekday profile.

    Silent below FORECAST_MIN_DAYS of history: a model with no data is
    decoration. Unknown weekdays fall back to the profile's known-day
    average. When access ends before the reset, the projection stops at
    the boundary the budget already stops at — one horizon per block,
    never two (the ledger's ┤, the budget's count, and this line must
    all describe the same span).
    """
    if not forecast or forecast["days_history"] < FORECAST_MIN_DAYS:
        return None
    tz = primary_tz() or datetime.now().astimezone().tzinfo
    profile = forecast["weekday_profile"]
    known = [v for v in profile.values() if v >= 0]
    if not known:
        return None
    fallback = sum(known) / len(known)

    horizon = entry["reset_dt"]
    truncated = access_end is not None and access_end < horizon
    if truncated:
        horizon = access_end

    projected = 0.0
    cursor = now.astimezone(tz)
    end = horizon.astimezone(tz)
    while cursor < end:
        day_end = (cursor + timedelta(days=1)).replace(
            hour=0, minute=0, second=0, microsecond=0
        )
        segment_end = min(day_end, end)
        frac = (segment_end - cursor).total_seconds() / 86400
        rate = profile.get(str(cursor.date().toordinal() % 7), -1)
        projected += (rate if rate >= 0 else fallback) * frac
        cursor = segment_end

    lands = min(999, entry["util"] + projected)
    span = "by period end" if truncated else "rest of week"
    return (
        f"forecast: +{projected:.0f}% {span} on your pattern"
        f" · lands ~{lands:.0f}% ({forecast['days_history']}d history)"
    )

# --- prepaid credits ---

def fetch_prepaid_credits(token: str, org_uuid: str, alias: str) -> dict | None:
    """Prepaid credit balance, 5-min TTL cache + retry-after backoff.

    Same cache/err file contract as statusline (prepaid_credits.cache /
    .err in the account dir) so the two tools share fetches instead of
    doubling the API's load.
    """
    adir = account_dir(alias)
    cache = adir / "prepaid_credits.cache"
    err = adir / "prepaid_credits.err"
    now_ts = datetime.now(timezone.utc).timestamp()

    cached = None
    try:
        if cache.is_file():
            cached = json.loads(cache.read_text())
            if now_ts - cached.get("fetched_at", 0) < CREDITS_TTL_SEC:
                return cached
    except (OSError, json.JSONDecodeError):
        cached = None
    try:
        if err.is_file() and now_ts < json.loads(err.read_text()).get("retry_at", 0):
            return cached
    except (OSError, json.JSONDecodeError):
        pass

    try:
        resp = httpx.get(
            f"https://api.anthropic.com/api/oauth/organizations/{org_uuid}/prepaid/credits",
            headers={**oauth_headers(token), "x-organization-uuid": org_uuid},
            timeout=5,
        )
        resp.raise_for_status()
        data = resp.json()
        data["fetched_at"] = int(now_ts)
        adir.mkdir(parents=True, exist_ok=True)
        cache.write_text(json.dumps(data, separators=(",", ":")))
        err.unlink(missing_ok=True)
        return data
    except httpx.HTTPStatusError as e:
        retry_after = e.response.headers.get("retry-after")
        backoff = int(retry_after) if (retry_after or "").isdigit() else 300
        try:
            adir.mkdir(parents=True, exist_ok=True)
            err.write_text(json.dumps({"retry_at": int(now_ts + backoff)}))
        except OSError:
            pass
        LOGGER.debug("prepaid credits fetch failed (%d)", e.response.status_code)
        return cached
    except httpx.HTTPError as e:
        LOGGER.debug("prepaid credits fetch failed: %s", e)
        return cached


# --- shared fetch pool ---
# statusline and ccpace observe the same account; whichever fetched last
# serves both. usage.cache / profile.cache are statusline's shapes,
# written atomically (tmp+rename) so either tool can read mid-write.

SHARED_USAGE_FRESH_SEC = 60


def read_shared_usage_cache(alias: str, max_age: int = SHARED_USAGE_FRESH_SEC) -> dict | None:
    """A fresh usage.cache is a fetch someone already made — use it."""
    path = account_dir(alias) / "usage.cache"
    try:
        data = json.loads(path.read_text())
        age = datetime.now(timezone.utc).timestamp() - data.get("fetched_at", 0)
        if 0 <= age <= max_age:
            data["_from_shared_cache"] = True
            return data
    except (OSError, json.JSONDecodeError, ValueError):
        pass
    return None


def write_shared_usage_cache(alias: str, usage: dict) -> None:
    """Publish our fetch so statusline's next render skips its own."""
    path = account_dir(alias) / "usage.cache"
    try:
        payload = {k: v for k, v in usage.items() if not k.startswith("_")}
        payload["fetched_at"] = int(datetime.now(timezone.utc).timestamp())
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(f".tmp.{os.getpid()}")
        tmp.write_text(json.dumps(payload, separators=(",", ":")))
        tmp.replace(path)
    except OSError as e:
        LOGGER.debug("usage.cache write failed: %s", e)


def read_shared_profile_cache(alias: str, max_age: int = PROFILE_TTL_SEC) -> dict | None:
    """profile.cache is the raw profile; its mtime is the fetch time
    (statusline's TTL rule — the file carries no fetched_at)."""
    path = account_dir(alias) / "profile.cache"
    try:
        if datetime.now(timezone.utc).timestamp() - path.stat().st_mtime <= max_age:
            return json.loads(path.read_text())
    except (OSError, json.JSONDecodeError, ValueError):
        pass
    return None


def write_shared_profile_cache(alias: str, profile: dict) -> None:
    path = account_dir(alias) / "profile.cache"
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(f".tmp.{os.getpid()}")
        tmp.write_text(json.dumps(profile, separators=(",", ":")))
        tmp.replace(path)
    except OSError as e:
        LOGGER.debug("profile.cache write failed: %s", e)


def attach_prepaid(
    data: dict | None, profile: dict | None, token: str | None, label: str
) -> None:
    """Attach the prepaid balance to the usage payload for display."""
    org_uuid = ((profile or {}).get("organization") or {}).get("uuid") or ""
    if data is not None and org_uuid and token:
        credits = fetch_prepaid_credits(token, org_uuid, get_alias_from_label(label))
        if credits:
            data["_prepaid"] = credits


def burn_glyph(cost: float) -> str:
    """Height ∝ 7d points a window cost. Sub-1% reads as idle, not as fill."""
    if cost < 1:
        return "·"
    for hi, glyph in ((2, "▁"), (4, "▂"), (6, "▃"), (8, "▄"), (11, "▅"),
                      (15, "▆"), (20, "▇")):
        if cost <= hi:
            return glyph
    return "█"


def format_window_ledger(
    entry: dict,
    costs: dict[int, float],
    span: tuple[float, float] | None,
    now: datetime,
    width: int = WEEK_STRIP_WIDTH,
    color: bool = True,
    access_end: datetime | None = None,
) -> str:
    """The 7d period as its 5h windows: what each cost, and what is left.

    One cell per 5h slot on a fixed grid from the period start:
      ▁▂▃▄▅▆▇█  a window that ran, height ∝ 7d points it burned
      ·         a window that ran nothing (store was watching, saw no burn)
      ░         unknown — outside the sample store's coverage
      ▮         the window you are in now
      ▫         a window still ahead of you
      ×         a window the 7d pool will not cover at the current pace
      ┤         access ends here — the strip stops, those windows are not yours
    Unknown and idle are deliberately different glyphs: drawing a gap in
    the record as an idle session is the one lie this surface must not
    tell.
    """
    period_start = (entry["reset_dt"] - timedelta(seconds=entry["length"])).timestamp()
    now_ts = now.timestamp()
    now_slot = int((now_ts - period_start) // WINDOW_5H_SEC)
    dry_slot = None
    cap_eta = entry.get("cap_eta")
    if cap_eta is not None and cap_eta < entry["reset_dt"]:
        dry_slot = int((cap_eta.timestamp() - period_start) // WINDOW_5H_SEC)
    end_slot = None
    if access_end is not None and access_end < entry["reset_dt"]:
        # first fully-unusable slot; the slot containing the end is still
        # (partially) spendable, so it keeps its normal glyph
        off = access_end.timestamp() - period_start
        end_slot = max(int((off + WINDOW_5H_SEC - 1) // WINDOW_5H_SEC), now_slot + 1)

    cells: list[tuple[str, str]] = []  # (glyph, role)
    for i in range(width):
        if end_slot is not None and i >= end_slot:
            cells.append(("┤", "end"))
            break
        if i < now_slot:
            slot_start = period_start + i * WINDOW_5H_SEC
            if i in costs:
                glyph = burn_glyph(costs[i])
                # a sampled window under 1% and an idle one both mean "cost
                # me nothing" — one glyph, one tint, no colour-only meaning
                cells.append((glyph, "idle" if glyph == "·" else "burn"))
            elif span and span[0] <= slot_start + WINDOW_5H_SEC and slot_start <= span[1]:
                cells.append(("·", "idle"))
            else:
                cells.append(("░", "unknown"))
        elif i == now_slot:
            cells.append(("▮", "now"))
        elif dry_slot is not None and i >= dry_slot:
            cells.append(("×", "dry"))
        else:
            cells.append(("▫", "future"))

    if not color or not supports_color():
        return "".join(g for g, _ in cells)

    tint = {
        "burn": GREEN if entry["util"] < 60 else (YELLOW if entry["util"] < 80 else RED),
        "idle": DIM,
        "unknown": DIM,
        "now": BOLD,
        "future": "",
        "dry": RED,
        "end": YELLOW,
    }
    out, current = [], None
    for glyph, role in cells:
        if role != current:
            out.append(RESET + tint[role])
            current = role
        out.append(glyph)
    return "".join(out) + RESET


def format_duration(seconds: int) -> str:
    """Format duration in human-readable format."""
    if seconds <= 0:
        return "expired"

    days = seconds // 86400
    hours = (seconds % 86400) // 3600
    mins = (seconds % 3600) // 60

    if days > 0:
        return f"{days}d {hours}h"
    elif hours > 0:
        return f"{hours}h {mins}m"
    else:
        return f"{mins}m"


def parse_reset_dt(value: str | None) -> datetime | None:
    """Parse ISO resets_at timestamp. Returns None on missing/invalid."""
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return None


def analyze_windows(data: dict, now: datetime) -> list[dict]:
    """Derive timing/pace metrics per rate-limit window.

    Window start is not in the API but is derivable: start = resets_at - length.
    pace = usage_fraction / elapsed_fraction (>1 means on track to cap early).
    cap_eta = linear projection of when utilization hits 100%.
    """
    windows: list[dict] = []

    def add(
        name: str, util: int, resets_at: str | None, length: int, active: bool = False
    ) -> None:
        reset_dt = parse_reset_dt(resets_at)
        if not reset_dt:
            return
        remaining = (reset_dt - now).total_seconds()
        if remaining <= 0 or remaining > length:
            return
        elapsed = length - remaining
        elapsed_frac = elapsed / length
        pace = None
        cap_eta = None
        if util > 0 and elapsed_frac >= PACE_MIN_ELAPSED_FRAC:
            pace = (util / 100) / elapsed_frac
            if util < 100:
                cap_eta = now + timedelta(seconds=(100 - util) * elapsed / util)
        windows.append(
            {
                "name": name,
                "util": util,
                "reset_dt": reset_dt,
                "length": length,
                "remaining": remaining,
                "elapsed_frac": elapsed_frac,
                "pace": pace,
                "cap_eta": cap_eta,
                "active": active,
            }
        )

    five = data.get("five_hour") or {}
    add(
        "5h", int(five.get("utilization", 0) or 0), five.get("resets_at"), WINDOW_5H_SEC
    )

    seven = data.get("seven_day") or {}
    add(
        "7d",
        int(seven.get("utilization", 0) or 0),
        seven.get("resets_at"),
        WINDOW_7D_SEC,
    )

    for lim in data.get("limits") or []:
        scope = lim.get("scope")
        if not scope:
            continue
        model = (scope.get("model") or {}).get("display_name") or lim.get("kind") or "??"
        kind = lim.get("kind") or ""
        length = (
            WINDOW_5H_SEC if ("five" in kind or "session" in kind) else WINDOW_7D_SEC
        )
        add(
            model,
            int(lim.get("percent", 0) or 0),
            lim.get("resets_at"),
            length,
            bool(lim.get("is_active")),
        )

    return windows


def format_cap_eta(dt: datetime, short: bool) -> str:
    """Format projected cap time; short (HH:MM) for 5h windows, weekday for 7d."""
    formatted = format_reset_short(dt) if short else format_reset_weekday(dt)
    return formatted.lstrip("@")


def build_advice(
    data: dict, windows: list[dict], access: tuple[datetime, str] | None = None
) -> list[tuple[str, str]]:
    """Derive pace warnings and weekly budgeting hints from window metrics.

    access = (end, note) truncates the budget when paid access stops
    before the 7d reset — a stated trial end, or the derived sub period
    end assumed binding (see get_access_end). Windows you cannot spend
    are not budget. Returns [(level, message)] with level "warn" or "info".
    """
    access_end, access_note = access if access else (None, "")
    advice: list[tuple[str, str]] = []
    seven_warned = False

    for w in windows:
        name, util, pace = w["name"], w["util"], w["pace"]
        if util >= 100:
            advice.append(
                (
                    "warn",
                    f"{name} capped - resets in {format_duration(int(w['remaining']))} "
                    f"({format_reset_weekday(w['reset_dt'])})",
                )
            )
            if name == "7d":
                seven_warned = True
            continue
        if (
            pace is not None
            and pace >= PACE_WARN_RATIO
            and util >= PACE_WARN_MIN_UTIL
            and w["cap_eta"] is not None
            and w["cap_eta"] < w["reset_dt"]
        ):
            short = w["length"] <= WINDOW_5H_SEC
            gap = (w["reset_dt"] - w["cap_eta"]).total_seconds()
            msg = (
                f"{name} pace {pace:.1f}x - cap ~{format_cap_eta(w['cap_eta'], short)}, "
                f"{format_duration(int(gap))} before reset"
            )
            if name == "7d":
                extra = data.get("extra_usage") or {}
                msg += (
                    "; then extra usage billing"
                    if extra.get("is_enabled")
                    else "; then hard stop until reset"
                )
                seven_warned = True
            advice.append(("warn", msg))

    # budget line: one frame, left to right — runway, what even looks like,
    # where you land; degrades when the week is nearly over
    seven = next((w for w in windows if w["name"] == "7d"), None)
    if seven:
        # same helper the strip anchors on: the number and the countable
        # cells right of │ can never disagree
        sessions_left = windows_left(seven)
        capped_by_end = False
        if access_end and access_end < seven["reset_dt"]:
            until_end = (access_end - (seven["reset_dt"] - timedelta(
                seconds=seven["remaining"]
            ))).total_seconds()
            end_windows = max(0, int((until_end + WINDOW_5H_SEC - 1) // WINDOW_5H_SEC))
            if end_windows < sessions_left:
                sessions_left = end_windows
                capped_by_end = True
        headroom = 100 - seven["util"]
        if sessions_left <= 1:
            parts = ["budget: last window"]
            if headroom > 0:
                parts.append(f"{headroom}% left")
        else:
            parts = [f"budget: ~{sessions_left} windows left"]
            if headroom > 0:
                parts.append(f"{headroom / sessions_left:.1f}%/window stays even")
        if capped_by_end:
            # the quota outlives your access: say which boundary bit
            parts.append(access_note or f"access ends {format_cap_eta(access_end, False)}")
        if seven["pace"] is not None and not seven_warned and not capped_by_end:
            reset_str = format_reset_weekday(seven["reset_dt"]).lstrip("@")
            # "heading" is the shared landing verb across surfaces
            # (statusline.sh advisor + report say the same word)
            parts.append(f"heading ~{seven['pace'] * 100:.0f}% at reset {reset_str}")
        advice.append(("info", " · ".join(parts)))

    return advice


def advice_segment_hot(seg: str) -> bool:
    """Should this budget-line segment stop the eye?

    Access boundaries always do; a landing projection only once it
    heads for >= 90% of the pool.
    """
    if seg.startswith(("period ends", "trial ends", "access ends", "sub ")):
        return True
    if seg.startswith(("heading ~", "lands ~")):
        try:
            return float(seg.split("~")[1].split("%")[0]) >= 90
        except (IndexError, ValueError):
            return False
    return False


def info_usage_single(
    token: str, label: str, raw: bool, trace: bool = False
) -> tuple[int, dict | None]:
    """Fetch usage stats for a single account."""
    try:
        url = "https://api.anthropic.com/api/oauth/usage"
        headers = oauth_headers(token)

        log_http(
            "REQUEST",
            {
                "method": "GET",
                "url": url,
                "headers": {
                    k: (f"Bearer sk-...{v[-4:]}" if k.lower() == "authorization" else v)
                    for k, v in headers.items()
                },
            },
            trace,
        )

        resp = httpx.get(url, headers=headers, timeout=30)
        resp.raise_for_status()
        data = resp.json()

        log_http(
            "RESPONSE",
            {
                "status": resp.status_code,
                "headers": dict(resp.headers),
                "body": data,
            },
            trace,
        )

        return EXIT_OK, data

    except httpx.HTTPStatusError as e:
        status = e.response.status_code
        LOGGER.error("%s: request failed (%d)", label, status)
        try:
            LOGGER.error("%s: %s", label, e.response.text)
        except Exception:
            pass
        return EXIT_RUNTIME, None
    except httpx.HTTPError as e:
        LOGGER.error("%s: %s: %s", label, type(e).__name__, e)
        return EXIT_RUNTIME, None


async def fetch_profile_async(
    client: httpx.AsyncClient, token: str, label: str
) -> dict | None:
    """Fetch profile for a single credential. Returns profile dict or None."""
    url = "https://api.anthropic.com/api/oauth/profile"
    headers = axios_headers(token, content_type=True, no_cache=True)
    try:
        resp = await client.get(url, headers=headers)
        resp.raise_for_status()
        return resp.json()
    except httpx.HTTPError as e:
        LOGGER.debug("%s: profile fetch failed: %s", label, e)
        return None


async def fetch_all_profiles_async(
    credentials: list[tuple[Path, str]],
) -> dict[str, dict | None]:
    """Fetch profiles for all credentials sequentially."""
    results = {}
    async with httpx.AsyncClient(limits=HTTP_LIMITS, timeout=HTTP_TIMEOUT) as client:
        for cred_path, token in credentials:
            label = str(cred_path)
            alias = get_alias_from_label(label)
            if profile := read_shared_profile_cache(alias):
                results[label] = profile
                continue
            profile = await fetch_profile_async(client, token, label)
            results[label] = profile
            if profile:
                write_shared_profile_cache(alias, profile)
    return results


async def info_usage_single_async(
    client: httpx.AsyncClient, token: str, label: str, trace: bool = False
) -> tuple[int, dict | None]:
    """Async fetch usage stats for a single account."""
    url = "https://api.anthropic.com/api/oauth/usage"
    headers = oauth_headers(token)

    log_http(
        "REQUEST",
        {
            "method": "GET",
            "url": url,
            "headers": {
                k: (f"Bearer sk-...{v[-4:]}" if k.lower() == "authorization" else v)
                for k, v in headers.items()
            },
        },
        trace,
    )

    try:
        resp = await client.get(url, headers=headers)
        resp.raise_for_status()
        data = resp.json()

        log_http(
            "RESPONSE",
            {
                "status": resp.status_code,
                "headers": dict(resp.headers),
                "body": data,
            },
            trace,
        )

        return EXIT_OK, data

    except httpx.HTTPStatusError as e:
        status = e.response.status_code
        LOGGER.error("%s: request failed (%d)", label, status)
        try:
            LOGGER.error("%s: %s", label, e.response.text)
        except Exception:
            pass
        return EXIT_RUNTIME, None
    except httpx.HTTPError as e:
        LOGGER.error("%s: %s: %s", label, type(e).__name__, e)
        return EXIT_RUNTIME, None


async def fetch_all_usage_async(
    credentials: list[tuple[Path, str]], trace: bool = False
) -> dict[str, tuple[int, dict | None]]:
    """Fetch usage for all credentials sequentially."""
    results = {}
    async with httpx.AsyncClient(limits=HTTP_LIMITS, timeout=HTTP_TIMEOUT) as client:
        for cred_path, token in credentials:
            label = str(cred_path)
            alias = get_alias_from_label(label)
            if shared := read_shared_usage_cache(alias):
                results[label] = (EXIT_OK, shared)
                continue
            code, data = await info_usage_single_async(client, token, label, trace)
            results[label] = (code, data)
            if code == EXIT_OK and data:
                write_shared_usage_cache(alias, data)
    return results


def validate_executable(path_str: str) -> Path | None:
    """Validate that path is an executable file."""
    try:
        path = Path(path_str).expanduser().resolve()
    except (RuntimeError, ValueError):
        return None

    if not path.exists() or not path.is_file():
        return None
    if not os.access(path, os.X_OK):
        return None
    return path


def run_notifier(
    cmd: list[str], input_data: bytes | None = None, timeout: int = 5
) -> bool:
    """Run notification command with optional stdin. Returns success."""
    try:
        proc = subprocess.run(
            cmd,
            input=input_data,
            capture_output=True,
            timeout=timeout,
            check=False,
        )
        if proc.returncode != 0:
            stderr = proc.stderr.decode(errors="replace")[:200]
            LOGGER.warning("notifier exited %d: %s", proc.returncode, stderr)
            return False
        return True
    except subprocess.TimeoutExpired:
        LOGGER.warning("notifier timeout after %ds", timeout)
        return False
    except OSError as e:
        LOGGER.warning("notifier exec failed: %s", e)
        return False


def has_command(cmd: str) -> bool:
    """Check if command exists in PATH."""
    return (
        subprocess.run(
            ["command", "-v", cmd], capture_output=True, check=False
        ).returncode
        == 0
    )


def notify_macos(title: str, message: str) -> None:
    """Send macOS notification."""
    # Try terminal-notifier
    if has_command("terminal-notifier"):
        if run_notifier(["terminal-notifier", "-title", title, "-message", message]):
            return

    # Fallback to osascript with sound
    # Escape quotes for AppleScript
    escaped_title = title.replace('"', '\\"').replace("\\", "\\\\")
    escaped_msg = message.replace('"', '\\"').replace("\\", "\\\\")

    # Add sound for critical alerts (full/reset)
    sound_name = os.getenv("CCPACE_MACOS_SOUND", MACOS_ALERT_SOUND)
    sound = (
        f' sound name "{sound_name}"'
        if "full" in message.lower() or "reset" in message.lower()
        else ""
    )
    run_notifier(
        [
            "osascript",
            "-e",
            f'display notification "{escaped_msg}" with title "{escaped_title}"{sound}',
        ]
    )


def notify_linux(title: str, message: str) -> None:
    """Send Linux notification."""
    if not has_command("notify-send"):
        return
    urgency = "critical" if "full" in message.lower() else "normal"
    icon = "dialog-warning" if "full" in title.lower() else "dialog-information"
    run_notifier(["notify-send", "-u", urgency, "-i", icon, title, message])


# push channels, set once from flags/env at startup: {"ntfy": url, "bark": url}
NOTIFY_CHANNELS: dict[str, str] = {}

# event -> (ntfy priority, bark level); the two services grade differently
EVENT_SEVERITY = {
    "full": ("urgent", "critical"),
    "threshold": ("high", "timeSensitive"),
    "pace": ("high", "timeSensitive"),
    "reset": ("default", "active"),
    "delta": ("low", "passive"),
}


def notify_ntfy(url: str, title: str, message: str, event: str) -> None:
    """Push via ntfy: POST body to the topic URL (https://ntfy.sh/topic)."""
    priority, _ = EVENT_SEVERITY.get(event, ("default", "active"))
    try:
        httpx.post(
            url,
            content=message.encode(),
            headers={"Title": title, "Priority": priority, "Tags": "claude"},
            timeout=10,
        ).raise_for_status()
    except httpx.HTTPError as e:
        LOGGER.warning("ntfy push failed: %s", e)


def notify_bark(url: str, title: str, message: str, event: str) -> None:
    """Push via bark: POST to https://api.day.app/KEY/title/body."""
    from urllib.parse import quote

    _, level = EVENT_SEVERITY.get(event, ("default", "active"))
    try:
        httpx.post(
            f"{url.rstrip('/')}/{quote(title)}/{quote(message)}",
            params={"level": level, "group": PROG},
            timeout=10,
        ).raise_for_status()
    except httpx.HTTPError as e:
        LOGGER.warning("bark push failed: %s", e)


def format_notification_message(event: str, account: str, data: dict) -> str:
    """Format human-readable notification message for system fallback."""
    if event == "threshold":
        util = data.get("utilization", 0)
        thresh = data.get("threshold", 0)
        return f"{account}: {util}% usage (threshold: {thresh}%)"
    if event == "full":
        return f"{account}: quota full (100%)"
    if event == "delta":
        util = data.get("utilization", 0)
        delta = data.get("delta", 0)
        reset = data.get("reset_short", "")
        return f"{account}: {util}% (+{delta}%) reset {reset}"
    if event == "pace":
        window = data.get("window", "")
        pace = data.get("pace", 0)
        cap = data.get("cap_eta", "")
        return f"{account}: {window} pace {pace}x, cap ~{cap} before reset"
    if event == "reset":
        return f"{account}: quota reset at {data.get('reset_time', '')}"
    return f"{account}: {event}"


def send_notification(
    event: str,
    account: str,
    data: dict,
    notifier: str | None = None,
) -> None:
    """Fan out a notification to every configured channel.

    Events: "threshold", "full", "delta", "pace", "reset".
    Channels: ntfy/bark push (NOTIFY_CHANNELS, additive), then either a
    custom notifier script (JSON on stdin, replaces system notify) or
    the OS notification as the local channel.
    """
    payload = {"event": event, "account": account, "data": data}
    title = f"Claude {event.title()}"
    message = format_notification_message(event, account, data)

    if url := NOTIFY_CHANNELS.get("ntfy"):
        notify_ntfy(url, title, message, event)
    if url := NOTIFY_CHANNELS.get("bark"):
        notify_bark(url, title, message, event)

    if notifier:
        notifier_path = validate_executable(notifier)
        if not notifier_path:
            LOGGER.warning("notifier not executable: %s", notifier)
            return

        input_bytes = (json.dumps(payload) + "\n").encode()
        LOGGER.debug("sending %s notification for %s", event, account)
        if run_notifier([str(notifier_path)], input_data=input_bytes, timeout=10):
            # debug, not info: a log line inside the watch frame reads as
            # account state; the notifier's own delivery is its receipt
            LOGGER.debug("notified: %s %s", event, account)
        else:
            LOGGER.warning("notifier failed: %s %s", event, account)
        return

    # System fallback
    os_name = get_os()
    if os_name == "darwin":
        notify_macos(title, message)
    elif os_name == "linux":
        notify_linux(title, message)


def print_row_tag(tag: str, use_color: bool) -> None:
    """Print the fixed-width row label column (see ROW_BAR_INDENT)."""
    if use_color:
        print(f"{DIM}{tag:<5}{RESET}", end=" ")
    else:
        print(f"{tag:<5}", end=" ")


def format_delta_col(delta: int, use_color: bool, dim: bool = False) -> str:
    """Pinned delta column: '+N%' or blank, so absence never shifts the row."""
    text = f"+{delta}%" if delta > 0 else ""
    padded = f"{text:>5s}"
    if use_color and text:
        return f"{DIM if dim else RED}{padded}{RESET}"
    return padded


def format_pace_col(pace: float | None, use_color: bool) -> str:
    """Pinned pace column: 'N.Nx' or blank."""
    text = f"{pace:.1f}x" if pace is not None else ""
    padded = f"{text:>5s}"
    if use_color and pace is not None:
        pace_color = RED if pace >= 1.5 else YELLOW if pace >= PACE_WARN_RATIO else DIM
        return f"{pace_color}{padded}{RESET}"
    return padded


def print_window_line(
    tag: str,
    util: int,
    use_color: bool,
    entry: dict | None = None,
    delta: int = 0,
    dim_delta: bool = False,
) -> None:
    """Print one window row: tag, usage%, dual bar, remaining/reset, delta, pace."""
    print_row_tag(tag, use_color)

    time_pct = int(entry["elapsed_frac"] * 100) if entry else None
    pace = entry["pace"] if entry else None
    # the number inherits the bar's temperature once it matters; calm
    # rows keep a plain figure so hot ones actually stand out
    util_txt = f"{util:3d}%"
    if use_color and util >= 80:
        util_txt = f"{BOLD}{RED}{util_txt}{RESET}"
    elif use_color and util >= 60:
        util_txt = f"{YELLOW}{util_txt}{RESET}"
    line = f"{util_txt} {format_dual_bar(util, time_pct, pace, color=use_color)}"
    if entry:
        remain_str = format_duration(int(entry["remaining"]))
        if entry["length"] <= WINDOW_5H_SEC:
            reset_str = format_reset_short(entry["reset_dt"])
        else:
            reset_str = format_reset_weekday(entry["reset_dt"])
        line += f"  {remain_str:<8s} {reset_str:<12s}"
    else:
        line += f"  {'':8s} {'':12s}"
    line += f" {format_delta_col(delta, use_color, dim_delta)}"
    line += f" {format_pace_col(pace, use_color)}"
    print(line.rstrip())


def print_window_ledger_row(
    entry: dict,
    use_color: bool,
    alias: str = "",
    log_dir: Path | None = None,
    now: datetime | None = None,
    access_end: datetime | None = None,
    samples: list[tuple[float, float, float, str]] | None = None,
) -> None:
    """Print the 7d period's 5h windows as one row under the quota rows.

    Its own row, indented to the bar column: the quota rows answer "how
    much is left", this answers "where did it go, and what is still
    ahead". Keeping them separate lets each stay the shape its data
    actually is.
    """
    now = now or datetime.now(timezone.utc)
    if samples is None:
        samples = load_usage_samples(usage_store_paths(alias, log_dir)) if alias else []
    period_start = (entry["reset_dt"] - timedelta(seconds=entry["length"])).timestamp()
    costs, span = build_window_history(samples, period_start, WEEK_STRIP_WIDTH)
    ledger = format_window_ledger(
        entry, costs, span, now, color=use_color, access_end=access_end
    )
    print(" " * ROW_BAR_INDENT + ledger)


def print_no_session_line(tag: str, use_color: bool) -> None:
    print_row_tag(tag, use_color)
    print(f"     {format_bar(0, color=use_color)}  no session")


def format_usage_display(
    data: dict,
    label: str | None = None,
    prev_data: dict | None = None,
    profile: dict | None = None,
    cached: bool = False,
    log_dir: Path | None = None,
) -> None:
    """Format and print usage stats: one dual bar (usage + window-elapsed) per window.

    Three bands, each answering one question:
      quota rows  how much of THIS window is left — one grammar, one
                  alignment, no row shaped differently from its peers
      ledger      where the week went and what is still ahead, one cell
                  per 5h window (see format_window_ledger)
      notices     what to do about it

    Bar: █ both passed | ▓ usage ahead (hot) | ▒ time ahead (headroom) | ░ untouched.
    The ledger row needs WEEK_STRIP_MIN_COLS; narrow terminals keep the
    quota rows and drop it.

    Format (wide):
        ── [20x] Alias · period ends ~Aug 11 ──────────────────────────
        5h     22% ██░░░░░░░░  4h 11m   @20:59        +3%  1.4x
        7d     80% ████████▓░  3d 20h   @Thu 23 23:59       1.8x
        fable  97% █████████▓  3d 20h   @Wed 22 23:59       2.2x
                   ▁▂▄·▁▃█▂··▁▅▃▂·▁▂▄▃▁·▂▃▄▂▁▮▫▫××××
         !  7d pace 1.8x - cap ~Thu 23 09:00, 2d 15h before reset; then extra usage billing
                   budget: ~19 windows left · 1.1%/window stays even
    """
    now = datetime.now(timezone.utc)
    use_color = supports_color()
    windows = analyze_windows(data, now)
    by_name = {w["name"]: w for w in windows}
    access = get_access_end(profile, now)
    # is the access end the binding boundary? then it must catch the eye
    access_bites = bool(
        access
        and (seven_w := by_name.get("7d"))
        and access[0] < seven_w["reset_dt"]
    )

    # Header doubles as the account splitter: a full-width rule with the
    # identity riding on it — zero extra rows (vertical density is
    # protected on the glance surface), but each account block
    # gets an unmistakable edge. Alias color = worst pace in the block,
    # so "is anything hot?" is answered before a single row is read.
    if label:
        alias = get_alias_from_label(label)
        segs: list[tuple[str, str]] = []  # (plain, colored)
        if tier_plain := get_account_tier_prefix(profile, False):
            segs.append((tier_plain, get_account_tier_prefix(profile, use_color)))
        worst_pace = max(
            (w["pace"] for w in windows if w["pace"] is not None), default=0.0
        )
        if worst_pace >= 1.5:
            alias_color = f"{BOLD}{RED}"
        elif worst_pace >= PACE_WARN_RATIO:
            alias_color = f"{BOLD}{YELLOW}"
        else:
            alias_color = BOLD
        segs.append((alias, f"{alias_color}{alias}{RESET}"))
        if cached:
            segs.append(("(cached)", f"{DIM}(cached){RESET}"))
        # sub/period note rides the rule; when it truncates the budget it
        # turns yellow — that date is now the number the block runs on
        if sub_note := get_sub_note(profile, now):
            note_color = YELLOW if access_bites else DIM
            segs.append((f"· {sub_note}", f"{note_color}· {sub_note}{RESET}"))

        plain = " ".join(p for p, _ in segs)
        body = " ".join(c if use_color else p for p, c in segs)
        cols = shutil.get_terminal_size().columns
        rule_w = max(0, min(cols - 1, 78) - len(plain) - 4)
        h = "─" if supports_unicode() else "-"
        if use_color:
            print(f"\n{DIM}{h * 2}{RESET} {body} {DIM}{h * rule_w}{RESET}")
        else:
            print(f"\n{h * 2} {body} {h * rule_w}")
    else:
        print()

    def section_delta(section: str, util: int) -> int:
        if not prev_data:
            return 0
        prev = prev_data.get(section) or {}
        return util - int(prev.get("utilization", 0) or 0)

    # quota rows first, one grammar and one alignment for all of them:
    # 5h, 7d, then any model-scoped caps. Every row answers the same
    # question (how much of THIS window is left), so none of them gets a
    # different shape — the ledger below is where the other question lives.
    five = data.get("five_hour") or {}
    five_util = int(five.get("utilization", 0) or 0)
    entry = by_name.get("5h")
    if entry:
        print_window_line(
            "5h",
            five_util,
            use_color,
            entry=entry,
            delta=section_delta("five_hour", five_util),
        )
    else:
        print_no_session_line("5h", use_color)

    seven = data.get("seven_day") or {}
    seven_util = int(seven.get("utilization", 0) or 0)
    seven_entry = by_name.get("7d")
    if seven_entry:
        print_window_line(
            "7d",
            seven_util,
            use_color,
            entry=seven_entry,
            delta=section_delta("seven_day", seven_util),
        )
    else:
        print_no_session_line("7d", use_color)

    # legacy model windows (display only, no reset info)
    for section, tag in (("seven_day_sonnet", "so"), ("seven_day_opus", "op")):
        win = data.get(section)
        if win and win.get("utilization") is not None:
            util = int(win.get("utilization", 0) or 0)
            print_window_line(
                tag, util, use_color, delta=section_delta(section, util), dim_delta=True
            )

    # scoped limits (limits[] array; e.g. per-model weekly caps)
    prev_scoped = {}
    if prev_data:
        for lim in prev_data.get("limits") or []:
            scope = lim.get("scope")
            if scope:
                model = (scope.get("model") or {}).get("display_name") or lim.get("kind")
                prev_scoped[model] = int(lim.get("percent", 0) or 0)

    for lim in data.get("limits") or []:
        scope = lim.get("scope")
        if not scope:
            continue  # session/weekly_all already shown via five_hour/seven_day
        model = (scope.get("model") or {}).get("display_name") or lim.get("kind")
        util = int(lim.get("percent", 0) or 0)
        # the model name IS the label; a 2-char tag needed a suffix crutch
        tag = (model or "??").lower()[:5]
        print_window_line(
            tag,
            util,
            use_color,
            entry=by_name.get(model),
            delta=util - prev_scoped.get(model, util),
            dim_delta=True,
        )

    # extra usage credits (overage spend)
    extra = data.get("extra_usage") or {}
    if extra.get("is_enabled") or (extra.get("used_credits") or 0) > 0:
        util = int(extra.get("utilization", 0) or 0)
        used = extra.get("used_credits") or 0
        limit = extra.get("monthly_limit") or 0
        places = int(extra.get("decimal_places", 2) or 2)
        cur = extra.get("currency") or "USD"
        scale = 10**places
        print_row_tag("$$", use_color)
        bar = format_bar(util, color=use_color)
        print(f"{util:3d}% {bar}  {used / scale:.2f}/{limit / scale:.2f} {cur} extra")

    # prepaid credit balance (attached by the fetch layer; not an API
    # field). Zero-balance accounts stay silent — an empty wallet row on
    # every account is noise, not signal.
    prepaid = data.get("_prepaid") or {}
    amount = prepaid.get("amount")
    if amount and amount > 0:
        cur = prepaid.get("currency") or "USD"
        auto = (prepaid.get("auto_reload_settings") or {}).get("enabled")
        print_row_tag("bal", use_color)
        note = f"{amount / 100:.2f} {cur} prepaid" + (" · auto-reload" if auto else "")
        if use_color:
            print(f"{'':4s} {'':10s}  {DIM}{note}{RESET}")
        else:
            print(f"{'':4s} {'':10s}  {note}")

    # one sample load feeds both consumers: the ledger draws where the
    # week went, the forecast projects where the rest of it goes
    alias = get_alias_from_label(label) if label else ""
    samples = load_usage_samples(usage_store_paths(alias, log_dir)) if alias else []

    if seven_entry and shutil.get_terminal_size().columns >= WEEK_STRIP_MIN_COLS:
        print_window_ledger_row(
            seven_entry,
            use_color,
            now=now,
            access_end=access[0] if access else None,
            samples=samples,
        )

    advice = build_advice(data, windows, access)
    if seven_entry and samples and alias:
        acct_uuid = ((profile or {}).get("account") or {}).get("uuid") or ""
        forecast = weekday_burn_forecast(samples, acct_uuid, now)
        if forecast:
            write_forecast_cache(alias, forecast)
            if line := forecast_line(
                forecast, seven_entry, now, access[0] if access else None
            ):
                advice.append(("info", line))

    # advisor: pace warnings jut left for attention; the budget line sits
    # in the bar column, part of the block's reading order. Within it,
    # the segments that should stop the eye — a binding access end, a
    # landing projection at/near cap — surface in yellow; the rest stays dim.
    for level, msg in advice:
        if level == "warn":
            if use_color:
                print(f" {YELLOW}!  {msg}{RESET}")
            else:
                print(f" !  {msg}")
        else:
            indent = " " * ROW_BAR_INDENT
            if use_color:
                print(indent + f"{DIM} · {RESET}".join(
                    f"{YELLOW}{seg}{RESET}" if advice_segment_hot(seg)
                    else f"{DIM}{seg}{RESET}"
                    for seg in msg.split(" · ")
                ))
            else:
                print(f"{indent}{msg}")


def info_usage(
    credentials: list[tuple[Path, str]],
    raw: bool,
    trace: bool = False,
    log_dir: Path | None = None,
    no_log: bool = False,
) -> int:
    """Show API usage stats for all accounts."""
    # Use async for multiple credentials
    if len(credentials) > 1:
        results = asyncio.run(fetch_all_usage_async(credentials, trace))

        if raw:
            output = {label: data for label, (code, data) in results.items() if data}
            print(json.dumps(output, indent=2))
            return EXIT_OK

        # profiles carry tier + subscription period; watch mode already
        # fetches them, so the two surfaces show the same header
        profiles = asyncio.run(fetch_all_profiles_async(credentials))

        exit_code = EXIT_OK
        for cred_path, token in credentials:
            label = str(cred_path)
            code, data = results.get(label, (EXIT_RUNTIME, None))
            if code != EXIT_OK:
                exit_code = code
                continue
            if data:
                attach_prepaid(data, profiles.get(label), token, label)
                format_usage_display(data, label, profile=profiles.get(label))
                if not no_log:
                    log_usage_jsonl(label, data, False, log_dir, profiles.get(label))

        print()
        return exit_code

    # Single credential - use sync
    if raw:
        cred_path, token = credentials[0]
        label = str(cred_path)
        code, data = info_usage_single(token, label, raw, trace)
        if data:
            print(json.dumps({label: data}, indent=2))
        return EXIT_OK

    cred_path, token = credentials[0]
    alias = get_alias_from_label(str(cred_path))
    if data := read_shared_usage_cache(alias):
        code = EXIT_OK
    else:
        code, data = info_usage_single(token, str(cred_path), raw, trace)
        if code == EXIT_OK and data:
            write_shared_usage_cache(alias, data)
    if data:
        profiles = asyncio.run(fetch_all_profiles_async(credentials))
        attach_prepaid(data, profiles.get(str(cred_path)), token, str(cred_path))
        format_usage_display(data, str(cred_path), profile=profiles.get(str(cred_path)))
        if not no_log:
            log_usage_jsonl(
                str(cred_path), data, False, log_dir, profiles.get(str(cred_path))
            )

    print()
    return code


def move_cursor_home() -> None:
    if supports_color():
        print("\033[H\033[J", end="")


def get_max_utilization(data: dict, include_model_specific: bool = False) -> int:
    """Get maximum utilization percentage across all windows."""
    five = data.get("five_hour") or {}
    seven = data.get("seven_day") or {}
    five_util = int(five.get("utilization", 0) or 0)
    seven_util = int(seven.get("utilization", 0) or 0)
    if include_model_specific:
        sonnet = data.get("seven_day_sonnet") or {}
        opus = data.get("seven_day_opus") or {}
        sonnet_util = int(sonnet.get("utilization", 0) or 0)
        opus_util = int(opus.get("utilization", 0) or 0)
        scoped = [
            int(lim.get("percent", 0) or 0)
            for lim in data.get("limits") or []
            if lim.get("scope")
        ]
        return max(five_util, seven_util, sonnet_util, opus_util, *scoped)
    return max(five_util, seven_util)


def get_earliest_reset(data: dict) -> datetime | None:
    """Get earliest reset time from usage data."""
    now = datetime.now(timezone.utc)
    resets = []

    five = data.get("five_hour") or {}
    five_reset = five.get("resets_at")
    if five_reset:
        try:
            dt = datetime.fromisoformat(five_reset.replace("Z", "+00:00"))
            if dt > now:
                resets.append(dt)
        except (ValueError, AttributeError):
            pass

    seven = data.get("seven_day") or {}
    seven_reset = seven.get("resets_at")
    if seven_reset:
        try:
            dt = datetime.fromisoformat(seven_reset.replace("Z", "+00:00"))
            if dt > now:
                resets.append(dt)
        except (ValueError, AttributeError):
            pass

    return min(resets) if resets else None


def fetch_or_use_cache(
    label: str,
    cred_path: Path,
    cached_full_accounts: dict,
    credential_tokens: dict,
    now: datetime,
    trace: bool,
) -> tuple[dict | None, bool, int]:
    """Fetch usage data or use cache. Returns (data, used_cache, exit_code)."""
    if label in cached_full_accounts:
        cached_data, cached_reset = cached_full_accounts[label]
        if cached_reset is not None and now < cached_reset:
            LOGGER.debug("%s: cached", label)
            return cached_data, True, EXIT_OK

        LOGGER.debug("%s: reset reached, refreshing", label)
        del cached_full_accounts[label]

    refreshed_token = refresh_token_if_needed(cred_path)
    if refreshed_token:
        credential_tokens[label] = refreshed_token
        LOGGER.debug("%s: token refreshed", label)
    else:
        # Re-read from file to ensure we have the latest token
        file_token = get_token_from_file(cred_path)
        if file_token and file_token != credential_tokens.get(label):
            credential_tokens[label] = file_token
            LOGGER.debug("%s: token reloaded from file", label)

    token = credential_tokens.get(label)
    if not token:
        LOGGER.warning("%s: no valid token", label)
        return None, False, EXIT_RUNTIME

    alias = get_alias_from_label(label)
    if shared := read_shared_usage_cache(alias):
        return shared, False, EXIT_OK
    code, data = info_usage_single(token, label, False, trace)
    if code == EXIT_OK and data:
        write_shared_usage_cache(alias, data)
    return data, False, code


async def fetch_all_with_cache_async(
    credentials: list[tuple[Path, str]],
    cached_full_accounts: dict,
    credential_tokens: dict,
    now: datetime,
    trace: bool,
) -> dict[str, tuple[dict | None, bool, int]]:
    """Fetch usage for all credentials sequentially, respecting cache."""
    results = {}

    async with httpx.AsyncClient(limits=HTTP_LIMITS, timeout=HTTP_TIMEOUT) as client:
        for cred_path, _ in credentials:
            label = str(cred_path)

            # Check cache first
            if label in cached_full_accounts:
                cached_data, cached_reset = cached_full_accounts[label]
                if cached_reset is not None and now < cached_reset:
                    LOGGER.debug("%s: cached", label)
                    results[label] = (cached_data, True, EXIT_OK)
                    continue
                LOGGER.debug("%s: reset reached, refreshing", label)
                del cached_full_accounts[label]

            # Refresh token if needed (sync - local file operation)
            refreshed_token = refresh_token_if_needed(cred_path)
            if refreshed_token:
                credential_tokens[label] = refreshed_token
                LOGGER.debug("%s: token refreshed", label)
            else:
                file_token = get_token_from_file(cred_path)
                if file_token and file_token != credential_tokens.get(label):
                    credential_tokens[label] = file_token
                    LOGGER.debug("%s: token reloaded from file", label)

            token = credential_tokens.get(label)
            if not token:
                LOGGER.warning("%s: no valid token", label)
                continue

            alias = get_alias_from_label(label)
            if shared := read_shared_usage_cache(alias):
                results[label] = (shared, False, EXIT_OK)
                continue
            code, data = await info_usage_single_async(client, token, label, trace)
            results[label] = (data, False, code)
            if code == EXIT_OK and data:
                write_shared_usage_cache(alias, data)

    return results


def handle_notifications(
    label: str,
    max_util: int,
    threshold: int,
    notified_threshold: dict,
    notified_full: dict,
    notifier: str | None,
    data: dict | None = None,
    profile: dict | None = None,
) -> list[tuple]:
    """Handle threshold and full quota notifications. Returns list of notification tuples."""
    messages = []
    account = get_alias_from_label(label)
    timestamp = timestamp_now()
    tier = get_account_tier_label(profile)

    five = (data.get("five_hour") or {}) if data else {}
    seven = (data.get("seven_day") or {}) if data else {}
    five_util = int(five.get("utilization", 0) or 0)
    seven_util = int(seven.get("utilization", 0) or 0)
    window, reset_time = get_smart_reset_info(data) if data else ("", "")

    if max_util >= threshold and label not in notified_threshold:
        send_notification(
            "threshold",
            account,
            {
                "timestamp": timestamp,
                "tier": tier,
                "threshold": threshold,
                "five_util": five_util,
                "seven_util": seven_util,
                "reset_window": window,
                "reset_time": reset_time,
            },
            notifier,
        )
        notified_threshold[label] = True
        messages.append(("threshold", account, max_util, threshold))
    elif max_util < threshold and label in notified_threshold:
        del notified_threshold[label]

    if max_util >= 100 and label not in notified_full:
        send_notification(
            "full",
            account,
            {
                "timestamp": timestamp,
                "tier": tier,
                "five_util": five_util,
                "seven_util": seven_util,
                "reset_window": window,
                "reset_time": reset_time,
            },
            notifier,
        )
        notified_full[label] = True
        messages.append(("full", account, max_util, threshold))
    elif max_util < 100 and label in notified_full:
        del notified_full[label]

    return messages


def get_alias_from_label(label: str) -> str:
    """Extract alias from credential path: /path/to/work.credentials.json -> work"""
    stem = Path(label).stem  # work.credentials
    return stem.split(".")[0]  # work


def get_account_tier_sort_key(profile: dict | None) -> int:
    """Get sort key for account tier. Lower = higher priority.

    Order: 20x (0) > 5x (1) > max (2) > pro (3) > unknown (9)
    """
    if not profile:
        return 9

    org = profile.get("organization") or {}
    account = profile.get("account") or {}

    rate_tier = org.get("rate_limit_tier", "")
    org_type = org.get("organization_type", "")
    has_pro = account.get("has_claude_pro", False)

    if "20x" in rate_tier:
        return 0
    if "5x" in rate_tier:
        return 1
    if "max" in org_type:
        return 2
    if has_pro or org_type == "claude_pro":
        return 3
    return 9


def get_account_tier_label(profile: dict | None) -> str:
    """Get account tier label for notifications. Returns: 'max20x', 'max5x', 'max', 'pro', ''"""
    if not profile:
        return ""

    org = profile.get("organization") or {}
    account = profile.get("account") or {}

    rate_tier = org.get("rate_limit_tier", "")
    org_type = org.get("organization_type", "")
    has_pro = account.get("has_claude_pro", False)
    has_max = account.get("has_claude_max", False)

    if "20x" in rate_tier:
        return "max20x"
    if "5x" in rate_tier:
        return "max5x"
    if has_max or "max" in org_type:
        return "max"
    if has_pro or org_type == "claude_pro":
        return "pro"
    return ""


def get_account_tier_prefix(profile: dict | None, use_color: bool = False) -> str:
    """Get fixed-width account tier prefix with optional color.

    Returns: '[20x]', '[ 5x]', '[pro]' (5 chars fixed width)
    Colors: 20x=magenta, 5x=cyan, pro=dim
    """
    if not profile:
        return ""

    org = profile.get("organization") or {}
    account = profile.get("account") or {}

    rate_tier = org.get("rate_limit_tier", "")
    org_type = org.get("organization_type", "")
    has_pro = account.get("has_claude_pro", False)
    has_max = account.get("has_claude_max", False)

    if "20x" in rate_tier:
        label = "20x"
        color = MAGENTA if use_color else ""
    elif "5x" in rate_tier:
        label = " 5x"
        color = CYAN if use_color else ""
    elif has_pro or org_type == "claude_pro":
        label = "pro"
        color = DIM if use_color else ""
    elif has_max or "max" in org_type:
        label = "max"
        color = CYAN if use_color else ""
    else:
        return ""

    reset = RESET if use_color and color else ""
    return f"{color}[{label}]{reset}"


def get_trial_end(profile: dict | None) -> datetime | None:
    """A stated trial end — the one hard access end the API actually gives."""
    if not profile:
        return None
    org = profile.get("organization") or {}
    trial_end = org.get("claude_code_trial_ends_at")
    if not trial_end:
        return None
    try:
        return datetime.fromisoformat(trial_end.replace("Z", "+00:00"))
    except ValueError:
        return None


def derive_period_end(profile: dict | None, now: datetime) -> datetime | None:
    """Next monthly anniversary of subscription_created_at: the paid period end.

    No endpoint states a renewal, cancellation, or period end — re-verified
    2026-08-06: purchase-eligibility data only exists inside
    `credits_required` error payloads, and every billing-ish oauth path
    404s. Stripe bills monthly on the created_at anniversary, so this
    derivation is the end of the current paid period whether or not it
    renews. ~ marks it wherever it is shown.
    """
    if not profile:
        return None
    org = profile.get("organization") or {}
    created = org.get("subscription_created_at")
    if not created:
        return None
    try:
        anchor = datetime.fromisoformat(created.replace("Z", "+00:00"))
    except ValueError:
        return None
    tz = primary_tz() or datetime.now().astimezone().tzinfo
    anchor = anchor.astimezone(tz)
    now_tz = now.astimezone(tz)

    # next monthly anniversary of the subscription, clamped to month length
    year, month = now_tz.year, now_tz.month
    for _ in range(3):
        next_month_start = datetime(
            year + (month == 12), month % 12 + 1, 1, tzinfo=tz
        )
        days_in_month = (next_month_start - timedelta(days=1)).day
        candidate = now_tz.replace(
            year=year,
            month=month,
            day=min(anchor.day, days_in_month),
            hour=anchor.hour,
            minute=anchor.minute,
            second=0,
            microsecond=0,
        )
        if candidate > now_tz:
            return candidate
        month += 1
        if month > 12:
            month, year = 1, year + 1
    return None


def get_access_end(
    profile: dict | None, now: datetime
) -> tuple[datetime, str] | None:
    """End of paid access for budget math: (datetime, display note).

    A stated trial end wins; otherwise the derived period end is ASSUMED
    to be the end (operator ruling 2026-08-06, overriding the 08-04 scar:
    the API cannot say whether the sub renews, so the budget treats the
    derived boundary as binding rather than budgeting windows past a
    possible cancellation). The ~ in the note marks the assumption.
    """
    if trial_dt := get_trial_end(profile):
        return trial_dt, f"trial ends {trial_dt.strftime('%b')} {trial_dt.day}"
    if end := derive_period_end(profile, now):
        return end, f"period ends ~{end.strftime('%b')} {end.day}"
    return None


def get_sub_note(profile: dict | None, now: datetime) -> str:
    """Subscription note: 'period ends ~Aug 8', 'trial ends Aug 15', 'sub past_due'."""
    if not profile:
        return ""
    org = profile.get("organization") or {}
    if get_trial_end(profile) is None:
        status = org.get("subscription_status") or ""
        if status and status != "active":
            return f"sub {status}"
        if not status:
            return ""
    access = get_access_end(profile, now)
    return access[1] if access else ""


def primary_tz():
    """The display timezone: first CCPACE_TZ zone, else the local zone."""
    return DISPLAY_TZS[0][0]


def format_reset_local(dt: datetime | None) -> str:
    """Format reset time in the display timezone. e.g. 'Tue 2, 14:59'"""
    if not dt:
        return ""
    t = dt.astimezone(primary_tz())
    return f"{t.strftime('%a')} {t.day}, {t.strftime('%H:%M')}"


def format_reset_short(dt: datetime | None) -> str:
    """Format reset as @HH:MM in the display timezone."""
    if not dt:
        return ""
    return "@" + dt.astimezone(primary_tz()).strftime("%H:%M")


def format_reset_weekday(dt: datetime | None) -> str:
    """Format reset as @Weekday D HH:MM in the display timezone."""
    if not dt:
        return ""
    t = dt.astimezone(primary_tz())
    return f"@{t.strftime('%a')} {t.day} {t.strftime('%H:%M')}"


def timestamp_now() -> str:
    """Current HH:MM in the display timezone (notification payloads)."""
    return datetime.now(primary_tz() or timezone.utc).astimezone(primary_tz()).strftime("%H:%M")


def get_smart_reset_info(data: dict) -> tuple[str, str]:
    """Get smart reset window and time.

    Returns (window_label, reset_time_str).
    Logic: show the earliest reset time (whichever window resets first).
    """
    five = data.get("five_hour") or {}
    seven = data.get("seven_day") or {}
    five_reset = five.get("resets_at")
    seven_reset = seven.get("resets_at")

    five_dt = None
    seven_dt = None

    if five_reset:
        try:
            five_dt = datetime.fromisoformat(five_reset.replace("Z", "+00:00"))
        except (ValueError, AttributeError):
            pass

    if seven_reset:
        try:
            seven_dt = datetime.fromisoformat(seven_reset.replace("Z", "+00:00"))
        except (ValueError, AttributeError):
            pass

    # Return earliest reset
    if five_dt and seven_dt:
        if five_dt <= seven_dt:
            return "5h", format_reset_local(five_dt)
        return "7d", format_reset_local(seven_dt)
    elif five_dt:
        return "5h", format_reset_local(five_dt)
    elif seven_dt:
        return "7d", format_reset_local(seven_dt)

    return "", ""


def handle_delta_notification(
    label: str,
    data: dict,
    prev_data: dict | None,
    notifier: str | None,
    profile: dict | None = None,
) -> tuple[str, int] | None:
    """Send notification on 5h usage increase. Returns (account, delta) or None."""
    if not prev_data:
        return None

    five = data.get("five_hour") or {}
    prev_five = prev_data.get("five_hour") or {}
    five_util = int(five.get("utilization", 0) or 0)
    five_prev = int(prev_five.get("utilization", 0) or 0)
    five_delta = five_util - five_prev

    if five_delta <= 0:
        return None

    seven = data.get("seven_day") or {}
    prev_seven = prev_data.get("seven_day") or {}
    seven_util = int(seven.get("utilization", 0) or 0)
    seven_prev = int(prev_seven.get("utilization", 0) or 0)

    account = get_alias_from_label(label)
    window, reset_time = get_smart_reset_info(data)
    timestamp = timestamp_now()
    tier = get_account_tier_label(profile)

    send_notification(
        "delta",
        account,
        {
            "timestamp": timestamp,
            "tier": tier,
            "five_util": five_util,
            "five_prev": five_prev,
            "five_delta": five_delta,
            "seven_util": seven_util,
            "seven_prev": seven_prev,
            "reset_window": window,
            "reset_time": reset_time,
        },
        notifier,
    )
    return (account, five_delta)


def handle_pace_notifications(
    label: str,
    data: dict,
    notified_pace: dict,
    notifier: str | None,
    profile: dict | None = None,
) -> None:
    """Notify once per window instance when pace projects a cap before reset."""
    now = datetime.now(timezone.utc)
    account = get_alias_from_label(label)
    armed = notified_pace.setdefault(label, {})

    for w in analyze_windows(data, now):
        pace = w["pace"]
        if (
            pace is None
            or pace < PACE_WARN_RATIO
            or w["util"] < PACE_WARN_MIN_UTIL
            or w["util"] >= 100
            or w["cap_eta"] is None
            or w["cap_eta"] >= w["reset_dt"]
        ):
            continue
        reset_iso = w["reset_dt"].isoformat()
        if armed.get(w["name"]) == reset_iso:
            continue
        short = w["length"] <= WINDOW_5H_SEC
        send_notification(
            "pace",
            account,
            {
                "timestamp": timestamp_now(),
                "tier": get_account_tier_label(profile),
                "window": w["name"],
                "utilization": w["util"],
                "pace": round(pace, 2),
                "cap_eta": format_cap_eta(w["cap_eta"], short),
                "reset_time": format_reset_local(w["reset_dt"]),
            },
            notifier,
        )
        armed[w["name"]] = reset_iso


def rotate_usage_log(log_file: Path) -> None:
    """32 MiB cap, single .1 backup, mkdir lock — statusline's contract
    exactly, so either writer can rotate without eating the other's
    history. Readers read .1 then current."""
    try:
        if not log_file.is_file() or log_file.stat().st_size < USAGE_LOG_MAX_BYTES:
            return
        lock = Path(str(log_file) + ".rotate.lock")
        try:
            lock.mkdir()
        except OSError:
            return  # someone else is rotating
        try:
            log_file.replace(Path(str(log_file) + ".1"))
        finally:
            lock.rmdir()
    except OSError:
        pass


def log_usage_jsonl(
    label: str,
    usage: dict,
    cached: bool,
    log_dir: Path | None = None,
    account: dict | None = None,
) -> None:
    """Append a store-v1 "usage" record (docs/data.md).

    The shape is claude-code-statusline's record — typed, epoch
    timestamp, raw API sections verbatim — so both tools share one
    history. Cached (100%-cap) responses are not logged: the log
    records observations, not echoes.
    """
    if cached or usage.get("_from_shared_cache"):
        return
    target = log_dir if log_dir else account_dir(get_alias_from_label(label))
    try:
        target.mkdir(parents=True, exist_ok=True)
    except OSError:
        return

    acct = (account or {}).get("account") or {}
    org = (account or {}).get("organization") or {}
    entry = {
        "type": "usage",
        "timestamp": int(datetime.now(timezone.utc).timestamp()),
        "source": f"{PROG}/{__version__}",
        "session_id": None,
        "user": {
            "email": acct.get("email", ""),
            "name": acct.get("full_name") or acct.get("display_name") or "",
            "uuid": acct.get("uuid", ""),
            "display_name": acct.get("display_name", ""),
            "subscriptions": {
                "claude_pro": bool(acct.get("has_claude_pro")),
                "claude_max": bool(acct.get("has_claude_max")),
            },
        },
        "organization": {
            "name": org.get("name", ""),
            "type": org.get("organization_type", ""),
            "billing_type": org.get("billing_type", ""),
            "rate_limit_tier": org.get("rate_limit_tier", ""),
        },
        "five_hour": usage.get("five_hour"),
        "seven_day": usage.get("seven_day"),
        "seven_day_opus": usage.get("seven_day_opus"),
        "extra_usage": usage.get("extra_usage"),
        "limits": usage.get("limits") or [],
        "model": None,
        "predicted_end": None,
    }

    log_file = target / "usage.jsonl"
    rotate_usage_log(log_file)
    try:
        with log_file.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry, separators=(",", ":")) + "\n")
    except OSError as e:
        LOGGER.debug("failed to write usage log: %s", e)


def update_cache(
    label: str,
    max_util: int,
    data: dict,
    cached_full_accounts: dict,
) -> None:
    """Update cache for accounts at 100%."""
    if max_util < 100:
        if label in cached_full_accounts:
            del cached_full_accounts[label]
    else:
        reset_time = get_earliest_reset(data)
        cached_full_accounts[label] = (data, reset_time)
        if reset_time:
            LOGGER.debug(
                "%s: cached until %s",
                label,
                reset_time.astimezone().strftime("%m/%d %H:%M:%S"),
            )


def jittered(interval: int) -> int:
    """Interval with +/-10% noise: fleet watchers must not synchronize
    their polls into API-visible spikes."""
    spread = int(interval * WATCH_JITTER_FRAC)
    return max(MIN_WATCH_INTERVAL, interval + random.randint(-spread, spread))


def countdown_sleep(
    interval: int,
    earliest_reset: datetime | None,
    is_wait_mode: bool = False,
    reset_account: str | None = None,
) -> bool:
    """Sleep with real-time countdown display. Returns True if quit requested.

    Keys: 'r' = refresh immediately, 'q' = quit

    Layout: the stable facts (reset countdown, keys) read left; the
    per-second tickers (clock, refresh counter) sit dimmed at the right
    edge, out of the reading path but still the freshness proof.
    """
    primary_tz, _ = DISPLAY_TZS[0]
    use_color = supports_color()
    verb = "check" if is_wait_mode else "refresh"
    for i in range(interval):
        elapsed = i + 1
        refresh_remain = interval - elapsed
        clock = datetime.now(primary_tz).strftime("%H:%M:%S")

        left_parts = []
        if earliest_reset:
            reset_remain = int(
                (earliest_reset - datetime.now(timezone.utc)).total_seconds()
            )
            account_info = f" ({reset_account})" if reset_account else ""
            left_parts.append(f"reset {format_duration(reset_remain)}{account_info}")
        left_parts.append("r=refresh q=quit")
        left = " | ".join(left_parts)
        right = f"{clock} {verb} {refresh_remain}s"

        # every frame spans exactly cols-1 cells, overwriting the last —
        # no trailing pad (one extra char here wraps and scrolls the frame)
        cols = shutil.get_terminal_size().columns
        pad = cols - len(left) - len(right) - 1
        if pad >= 2:
            if use_color:
                msg = f"{left}{' ' * pad}{DIM}{right}{RESET}"
            else:
                msg = f"{left}{' ' * pad}{right}"
        else:
            msg = f"{left} | {right}"[: max(0, cols - 1)].ljust(max(0, cols - 1))

        sys.stdout.write(f"\r{msg}")
        sys.stdout.flush()

        key = check_keypress(timeout=1)
        if key:
            key_lower = key.lower()
            if key_lower == "r":
                sys.stdout.write("\r" + " " * (shutil.get_terminal_size().columns - 1) + "\r")
                sys.stdout.flush()
                LOGGER.debug("manual refresh triggered")
                return False
            elif key_lower == "q":
                sys.stdout.write("\r" + " " * (shutil.get_terminal_size().columns - 1) + "\r")
                sys.stdout.flush()
                LOGGER.debug("quit requested")
                return True

    sys.stdout.write("\r" + " " * (shutil.get_terminal_size().columns - 1) + "\r")
    sys.stdout.flush()
    return False


def print_watch_footer(
    all_full: bool,
    earliest_global_reset: datetime | None,
    earliest_reset_account: str | None,
    cached_count: int,
    total_count: int,
    interval: int,
    notifier: str | None,
    notified_threshold: dict,
    notified_full: dict,
    use_color: bool,
) -> bool:
    """Print watch mode footer with status. Returns True if quit requested.

    Chrome-free by design: the live countdown line (with its clock) is the
    freshness proof; config never changes between refreshes, so it prints
    once at startup, not per frame.
    """
    print()

    now = datetime.now(timezone.utc)
    if cached_count > 0:
        if use_color:
            print(
                f"{DIM}Cached: {cached_count}/{total_count} at 100% (no API polling){RESET}"
            )
        else:
            print(f"Cached: {cached_count}/{total_count} at 100% (no API polling)")

    if all_full and earliest_global_reset:
        remaining = int((earliest_global_reset - now).total_seconds())
        reset_local = format_multi_tz(earliest_global_reset)

        symbol = "⏳" if supports_unicode() else "~"
        if use_color:
            print(f"{YELLOW}{symbol} All quotas full. Next reset: {reset_local}{RESET}")
        else:
            print(f"{symbol} All quotas full. Next reset: {reset_local}")

        sleep_time = min(WAIT_MODE_CHECK_INTERVAL, remaining)
        if countdown_sleep(
            sleep_time,
            earliest_global_reset,
            is_wait_mode=True,
            reset_account=earliest_reset_account,
        ):
            return True

        if datetime.now(timezone.utc) >= earliest_global_reset:
            send_notification(
                "reset",
                earliest_reset_account or "all",
                {
                    "timestamp": timestamp_now(),
                    "reset_time": format_multi_tz(earliest_global_reset),
                },
                notifier,
            )
            notified_threshold.clear()
            notified_full.clear()
            symbol = "✓" if supports_unicode() else "+"
            if use_color:
                print(f"{GREEN}{symbol} Reset notification sent{RESET}")
            else:
                print(f"{symbol} Reset notification sent")
    else:
        return countdown_sleep(
            interval, earliest_global_reset, reset_account=earliest_reset_account
        )


def info_usage_watch(
    credentials: list[tuple[Path, str]],
    threshold: int,
    interval: int,
    notifier: str | None,
    log_dir: Path | None = None,
    trace: bool = False,
    no_log: bool = False,
) -> int:
    """Watch mode: monitor usage and notify on threshold/reset."""
    # Watch mode requires interactive terminal
    if not sys.stdout.isatty():
        LOGGER.error("watch mode requires interactive terminal (not a pipe/redirect)")
        return EXIT_USAGE

    interval = max(MIN_WATCH_INTERVAL, interval)
    use_color = supports_color()
    notified_threshold = {}
    notified_full = {}
    notified_pace = {}
    cached_full_accounts = {}
    credential_tokens = {str(path): token for path, token in credentials}
    previous_data = {}
    fail_backoff = 0  # exponential, resets on any successful cycle

    # Fetch profiles once at start (for display and logging)
    LOGGER.debug("fetching account profiles...")
    account_profiles: dict[str, dict | None] = asyncio.run(
        fetch_all_profiles_async(credentials)
    )

    # Sort credentials by tier (20x > 5x > max > pro), then by alias name
    credentials = sorted(
        credentials,
        key=lambda c: (
            get_account_tier_sort_key(account_profiles.get(str(c[0]))),
            get_alias_from_label(str(c[0])).lower(),
        ),
    )

    # config is not state: say it once, then let the frames speak
    config_line = (
        f"watching {len(credentials)} account(s)"
        f" | interval {interval}s | threshold {threshold}% | r=refresh q=quit"
    )
    if use_color:
        print(f"{DIM}{config_line}{RESET}")
    else:
        print(config_line)

    try:
        first_iteration = True
        while True:
            if first_iteration:
                first_iteration = False
            else:
                move_cursor_home()

            now = datetime.now(timezone.utc)
            all_full = True
            earliest_global_reset = None
            earliest_reset_account = None
            success_count = 0
            notification_messages = []

            # Fetch all credentials concurrently
            if len(credentials) > 1:
                fetch_results = asyncio.run(
                    fetch_all_with_cache_async(
                        credentials,
                        cached_full_accounts,
                        credential_tokens,
                        now,
                        trace,
                    )
                )
            else:
                # Single credential - use sync
                cred_path, _ = credentials[0]
                label = str(cred_path)
                data, used_cache, code = fetch_or_use_cache(
                    label,
                    cred_path,
                    cached_full_accounts,
                    credential_tokens,
                    now,
                    trace,
                )
                fetch_results = {label: (data, used_cache, code)}

            failed_accounts = []

            for cred_path, _ in credentials:
                label = str(cred_path)
                data, used_cache, code = fetch_results.get(label, (None, False, EXIT_RUNTIME))

                if code != EXIT_OK:
                    failed_accounts.append(get_alias_from_label(label))
                    continue

                if not data:
                    continue

                success_count += 1

                prev_data = previous_data.get(label)
                profile = account_profiles.get(label)
                if not used_cache:
                    attach_prepaid(data, profile, credential_tokens.get(label), label)
                format_usage_display(
                    data, label, prev_data, profile, used_cache, log_dir
                )

                if not no_log:
                    log_usage_jsonl(
                        label, data, used_cache, log_dir, account_profiles.get(label)
                    )

                if not used_cache:
                    previous_data[label] = data

                max_util = get_max_utilization(data, include_model_specific=False)

                if max_util < 100:
                    all_full = False

                update_cache(label, max_util, data, cached_full_accounts)

                msgs = handle_notifications(
                    label,
                    max_util,
                    threshold,
                    notified_threshold,
                    notified_full,
                    notifier,
                    data,
                    profile,
                )
                notification_messages.extend(msgs)

                if not used_cache:
                    handle_delta_notification(label, data, prev_data, notifier, profile)
                    handle_pace_notifications(
                        label, data, notified_pace, notifier, profile
                    )

                reset_time = get_earliest_reset(data)
                if reset_time and (
                    not earliest_global_reset or reset_time < earliest_global_reset
                ):
                    earliest_global_reset = reset_time
                    earliest_reset_account = Path(label).stem

            if notification_messages:
                print()
                symbol = "⚠" if supports_unicode() else "!"
                for ntype, account, util, thresh in notification_messages:
                    if ntype == "threshold":
                        if use_color:
                            print(
                                f"{YELLOW}{symbol} {account}: {util}% (threshold: {thresh}%){RESET}"
                            )
                        else:
                            print(f"{symbol} {account}: {util}% (threshold: {thresh}%)")
                    elif ntype == "full":
                        if use_color:
                            print(f"{RED}{symbol} {account}: quota full{RESET}")
                        else:
                            print(f"{symbol} {account}: quota full")

            if failed_accounts:
                print()
                symbol = "✗" if supports_unicode() else "X"
                failed_str = ", ".join(failed_accounts)
                if use_color:
                    print(f"{RED}{symbol} Failed: {failed_str}{RESET}")
                else:
                    print(f"{symbol} Failed: {failed_str}")

            if success_count == 0 and len(cached_full_accounts) == 0:
                # failures never tighten the loop: back off exponentially
                # (Retry-After-shaped errors land here too) until a cycle
                # succeeds, capped so recovery is never more than 15m away
                fail_backoff = min(
                    BACKOFF_MAX_SEC, (fail_backoff * 2) or BACKOFF_BASE_SEC
                )
                print()
                if use_color:
                    print(
                        f"{RED}All accounts failed - check network/credentials"
                        f" (retry in {fail_backoff}s){RESET}"
                    )
                else:
                    print(
                        "All accounts failed - check network/credentials"
                        f" (retry in {fail_backoff}s)"
                    )
                if countdown_sleep(fail_backoff, None):
                    break
                continue
            fail_backoff = 0

            if print_watch_footer(
                all_full,
                earliest_global_reset,
                earliest_reset_account,
                len(cached_full_accounts),
                len(credentials),
                jittered(interval),
                notifier,
                notified_threshold,
                notified_full,
                use_color,
            ):
                break

    except KeyboardInterrupt:
        pass

    print("\nMonitoring stopped.")
    return EXIT_OK


# --- cli ---

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog=PROG,
        description=__doc__.split("\n", 2)[1],
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )

    p.add_argument(
        "-f",
        "--file",
        type=Path,
        action="append",
        dest="files",
        nargs="+",
        metavar="PATH",
        help="credential file(s) (globs ok: -f ~/.claude/.credentials*.json); "
        "default: discover ~/.claude/.credentials*.json",
    )
    p.add_argument(
        "-w", "--watch", action="store_true", help="watch mode: live TUI, notifications"
    )
    p.add_argument(
        "--interval",
        type=int,
        default=DEFAULT_WATCH_INTERVAL,
        metavar="SEC",
        help=f"watch poll interval (default {DEFAULT_WATCH_INTERVAL}s, min "
        f"{MIN_WATCH_INTERVAL}s, +/-10%% jitter; env CCPACE_INTERVAL)",
    )
    p.add_argument(
        "--threshold",
        type=int,
        default=DEFAULT_WATCH_THRESHOLD,
        metavar="PCT",
        help=f"notify at utilization %% (default {DEFAULT_WATCH_THRESHOLD}; "
        "env CCPACE_THRESHOLD)",
    )
    p.add_argument(
        "--ntfy",
        metavar="URL",
        help="ntfy topic URL for push notifications (env CCPACE_NTFY)",
    )
    p.add_argument(
        "--bark",
        metavar="URL",
        help="bark endpoint, https://api.day.app/KEY (env CCPACE_BARK)",
    )
    p.add_argument(
        "--notifier",
        metavar="PATH",
        help="custom notifier script, JSON on stdin (env CCPACE_NOTIFIER)",
    )
    p.add_argument(
        "--log-dir",
        metavar="DIR",
        help="override sample-store dir (default: shared store, docs/data.md)",
    )
    p.add_argument(
        "--no-log", action="store_true", help="do not write usage samples"
    )
    p.add_argument("--profile", action="store_true", help="show account profile JSON")
    p.add_argument("--hello", action="store_true", help="API health check (no auth)")
    p.add_argument(
        "--raw", "--json", dest="raw", action="store_true", help="raw usage JSON"
    )
    p.add_argument(
        "-q", "--quiet", action="store_true", help="suppress non-error output"
    )
    p.add_argument(
        "-v",
        "--verbose",
        action="count",
        default=0,
        help="increase verbosity (-v debug, -vv HTTP trace)",
    )
    p.add_argument("-V", "--version", action="version", version=f"{PROG} {__version__}")
    return p


def env_int(name: str, fallback: int) -> int:
    if raw := os.getenv(name):
        try:
            return int(raw)
        except ValueError:
            LOGGER.warning("invalid %s: %s", name, raw)
    return fallback


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    setup_logging(args.quiet, args.verbose)
    signal.signal(signal.SIGINT, lambda *_: sys.exit(EXIT_INTERRUPT))

    if args.hello:
        return info_hello()

    cred_files = None
    if args.files:
        flattened = []
        for item in args.files:
            if isinstance(item, list):
                flattened.extend(item)
            else:
                flattened.append(item)
        cred_files = flattened if flattened else None
    explicit_file = args.files is not None

    if url := args.ntfy or os.getenv("CCPACE_NTFY"):
        NOTIFY_CHANNELS["ntfy"] = url
    if url := args.bark or os.getenv("CCPACE_BARK"):
        NOTIFY_CHANNELS["bark"] = url
    notifier = args.notifier or os.getenv("CCPACE_NOTIFIER")

    log_dir = Path(args.log_dir).expanduser() if args.log_dir else None

    credentials = get_all_credentials(cred_files, explicit_file)
    trace = args.verbose >= 2

    if args.profile:
        return info_profile(credentials)

    if args.watch:
        if args.raw:
            LOGGER.error("--watch cannot be used with --raw")
            return EXIT_USAGE
        return info_usage_watch(
            credentials,
            env_int("CCPACE_THRESHOLD", args.threshold),
            env_int("CCPACE_INTERVAL", args.interval),
            notifier,
            log_dir,
            trace,
            no_log=args.no_log,
        )

    return info_usage(credentials, args.raw, trace, log_dir, args.no_log)


if __name__ == "__main__":
    raise SystemExit(main())
