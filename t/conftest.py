"""Test fixtures for ccpace.

Every test that touches state gets its own CCPACE_DATA_DIR. The real store
is `~/.claude/statusline/` — a live, shared, multi-account directory — and a
test that writes there is not a test, it is a bug report someone else files.
"""

from __future__ import annotations

import importlib.util
import json
import os
import sys
from collections.abc import Iterable
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


def _load_ccpace():
    """Import ccpace.py by path: it is a single file, not an installed package."""
    spec = importlib.util.spec_from_file_location("ccpace", ROOT / "ccpace.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules["ccpace"] = module
    spec.loader.exec_module(module)
    return module


cc = _load_ccpace()


@pytest.fixture
def store(tmp_path, monkeypatch):
    """An isolated data root, and a clean identity to go with it.

    The account tag is read from the environment (deva exports DEVA_AUTH_TAG
    into every container, this repo's own dev loop included) — leaking it in
    would put the fixture's writes under accounts/<tag>/ and the reads at the
    root, so nothing would ever be found.
    """
    for var in (
        "STATUSLINE_ACCOUNT",
        "DEVA_AUTH_TAG",
        "DEVA_AUTH_METHOD",
        "DEVA_AUTH_DETAILS",
    ):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("CCPACE_DATA_DIR", str(tmp_path))
    cc._SAMPLE_CACHE.clear()
    return tmp_path


@pytest.fixture
def utc_now():
    """A fixed 'now': a Wednesday 09:00 UTC, so weekday math is readable."""
    return datetime(2026, 8, 26, 9, 0, tzinfo=timezone.utc)


@pytest.fixture
def utc_tz(monkeypatch):
    """Pin the display zone to UTC.

    `primary_tz()` reads DISPLAY_TZS, a module-level list built at import
    from CCPACE_TZ. Anything that buckets by local hour or local day — the
    envelope, the walk, the awake count — otherwise reads the machine's zone,
    and a test that says 03:00 means 03:00 somewhere the runner is not.
    """
    monkeypatch.setattr(cc, "DISPLAY_TZS", [(timezone.utc, "UTC")])
    return timezone.utc


def sample(ts: float, util: float, *, uuid: str = "acct-A", seven_reset: float = 0.0,
           five_reset: float = 0.0) -> cc.Sample:
    return cc.Sample(ts, five_reset or ts + 3600, util, uuid, seven_reset)


def seven_entry(util: int, remaining: float, now: datetime) -> dict:
    """A 7d window as analyze_windows would hand it over."""
    return {
        "name": "7d",
        "util": util,
        "reset_dt": now + timedelta(seconds=remaining),
        "length": cc.WINDOW_7D_SEC,
        "remaining": remaining,
        "elapsed_frac": (cc.WINDOW_7D_SEC - remaining) / cc.WINDOW_7D_SEC,
        "pace": None,
        "cap_eta": None,
        "active": False,
    }


def five_entry(util: int, remaining: float, now: datetime) -> dict:
    return {
        "name": "5h",
        "util": util,
        "reset_dt": now + timedelta(seconds=remaining),
        "length": cc.WINDOW_5H_SEC,
        "remaining": remaining,
        "elapsed_frac": (cc.WINDOW_5H_SEC - remaining) / cc.WINDOW_5H_SEC,
        "pace": None,
        "cap_eta": None,
        "active": False,
    }


def scoped_entry(
    name: str, util: int, remaining: float, now: datetime, *, active: bool = False
) -> dict:
    """A model-scoped weekly cap, as analyze_windows would hand it over.

    Same length and (by default) the same wall as the 7d — that shared reset
    is what makes the two utilizations a ratio — with its own name, depth and
    is_active flag.
    """
    entry = seven_entry(util, remaining, now)
    entry["name"] = name
    entry["active"] = active
    return entry


def flat_profile(rate: float, *, days: int = 30, recent_24h: float = 0.0) -> dict:
    """A profile that burns the same every weekday — the walk is then pure
    arithmetic and a test can state the expected landing exactly."""
    return {
        "schema": cc.FORECAST_SCHEMA,
        "computed_at": 0,
        "days_history": days,
        "recent_24h": recent_24h,
        "recent_48h": recent_24h * 2,
        "weekday_profile": {str(d): rate for d in range(7)},
    }


def hour_shape(rest_hours: Iterable[int], *, floor: float = 0.1) -> dict[str, float]:
    """A hand-built `hour_profile` with mean EXACTLY 1: `rest_hours` sit at
    the floor, the waking hours share what is left. Eight rest hours give
    1.45 each — the shape the walk is supposed to notice."""
    rest = set(rest_hours)
    waking = (24 - len(rest) * floor) / (24 - len(rest))
    return {str(h): floor if h in rest else waking for h in range(24)}


def write_cache(root: Path, payload: dict) -> Path:
    path = root / "forecast.cache"
    path.write_text(json.dumps(payload))
    return path
