"""Watch polling: an optimization may never freeze a still-moving account."""

from datetime import datetime, timedelta, timezone

from conftest import cc


def usage(
    *,
    five: int = 20,
    seven: int = 40,
    scoped: int | None = None,
    extra: bool = False,
) -> dict:
    reset_5h = (datetime.now(timezone.utc) + timedelta(hours=2)).isoformat()
    reset_7d = (datetime.now(timezone.utc) + timedelta(days=2)).isoformat()
    limits = []
    if scoped is not None:
        limits.append(
            {
                "kind": "weekly_scoped",
                "percent": scoped,
                "resets_at": reset_7d,
                "scope": {"model": {"display_name": "Fable"}},
            }
        )
    return {
        "five_hour": {"utilization": five, "resets_at": reset_5h},
        "seven_day": {"utilization": seven, "resets_at": reset_7d},
        "limits": limits,
        "extra_usage": {"is_enabled": extra},
    }


def test_only_an_unpaid_weekly_wall_is_terminal():
    assert not cc.account_is_terminal(usage(five=100, seven=53))
    assert not cc.account_is_terminal(usage(seven=53, scoped=100))
    assert cc.account_is_terminal(usage(seven=100))
    assert not cc.account_is_terminal(usage(seven=100, extra=True))


def test_five_hour_and_scoped_caps_never_freeze_the_account():
    terminal = {}
    cc.update_terminal_cache("work", usage(five=100, seven=53), terminal)
    assert terminal == {}
    cc.update_terminal_cache("work", usage(seven=53, scoped=100), terminal)
    assert terminal == {}


def test_weekly_wall_is_cached_until_its_own_reset(store):
    payload = usage(seven=100)
    terminal = {}
    cc.update_terminal_cache("work", payload, terminal)

    assert "work" in terminal
    cached = cc.terminal_cached_usage(
        "work", "work", terminal, datetime.now(timezone.utc)
    )
    assert cached is payload


def test_manual_refresh_evicts_a_terminal_snapshot():
    label = "work.credentials.json"
    terminal = {}
    cc.update_terminal_cache(label, usage(seven=100), terminal)
    cc.FORCE_FETCH.add(label)
    try:
        assert (
            cc.terminal_cached_usage(
                label, "work", terminal, datetime.now(timezone.utc)
            )
            is None
        )
        assert label not in terminal
    finally:
        cc.FORCE_FETCH.discard(label)


def test_newer_shared_writer_breaks_the_terminal_snapshot(store):
    label = "work.credentials.json"
    old = usage(seven=100)
    old["fetched_at"] = 1
    terminal = {}
    cc.update_terminal_cache(label, old, terminal)

    moved = usage(five=100, seven=54, extra=True)
    cc.write_shared_usage_cache("work", moved)
    got = cc.terminal_cached_usage(label, "work", terminal, datetime.now(timezone.utc))

    assert got["seven_day"]["utilization"] == 54
    assert got["extra_usage"]["is_enabled"] is True
    assert label not in terminal
