"""Calendar evidence, notification transitions, and interactive layout contracts."""

from __future__ import annotations

import asyncio
import json
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest
from conftest import cc
from ccpace_calendar import (
    AlertJournal,
    CalendarModel,
    DemoSource,
    LiveSource,
    Meter,
    Observation,
    ObservationStore,
    SCENARIOS,
    Snapshot,
    evaluate,
    intervals,
    meters,
)
from ccpace_tui import CalendarApp
from textual.widgets import DataTable, Static


def observation(at, used, reset, key="weekly_all"):
    return Observation(at, Meter(key, "7d all", used, reset, 7 * 86400))


def test_generic_meters_override_legacy_and_keep_scopes_separate(utc_now):
    reset = (utc_now + timedelta(days=2)).isoformat()
    payload = {
        "seven_day": {"utilization": 1, "resets_at": reset},
        "limits": [
            {"kind": "weekly_all", "percent": 38, "resets_at": reset},
            {
                "kind": "weekly_scoped",
                "percent": 57,
                "resets_at": reset,
                "scope": {"model": {"display_name": "Fable"}},
            },
        ],
    }
    assert [(m.key, m.used) for m in meters(payload)] == [
        ("weekly_all", 38),
        ("weekly_scoped:Fable", 57),
    ]
    assert (
        meters({"seven_day": {"utilization": float("nan"), "resets_at": reset}}) == []
    )


def test_reader_partitions_identity_and_skips_corrupt_rows(tmp_path, utc_now):
    path = tmp_path / "usage.jsonl"
    payload = {
        "seven_day": {
            "utilization": 12,
            "resets_at": (utc_now + timedelta(days=1)).isoformat(),
        }
    }
    rows = [
        {
            "type": "usage",
            "timestamp": utc_now.timestamp(),
            "user": {"uuid": uuid},
            **payload,
        }
        for uuid in ("acct-a", "acct-b", None)
    ]
    path.write_text("\n".join(json.dumps(row) for row in rows) + '\n[1]\n{"partial":\n')
    store = ObservationStore()
    assert len(store.read([path], "acct-a")) == 1
    assert store.read([path], "") == []
    assert store.read([path], "absent") == []


def test_sparse_delta_is_not_assigned_to_later_hour(utc_now):
    start = utc_now.timestamp()
    reset = start + 86400
    readings = [observation(start, 10, reset), observation(start + 7200, 30, reset)]
    snapshot = Snapshot("a", "a", "pro", {}, start + 7200, readings)
    model = CalendarModel(snapshot, utc_now + timedelta(hours=3))
    for hour in range(2):
        bucket = model.bucket(
            utc_now + timedelta(hours=hour), utc_now + timedelta(hours=hour + 1)
        )
        assert bucket.burn is None
        assert bucket.uncertain
    assert model.intervals[0].burn == 20


def test_observed_zero_and_unknown_are_different(utc_now):
    start = utc_now.timestamp()
    readings = [
        observation(start + offset, 10, start + 86400)
        for offset in (0, 900, 1800, 2700, 3600)
    ]
    model = CalendarModel(
        Snapshot("a", "a", "pro", {}, start + 3600, readings),
        utc_now + timedelta(hours=2),
    )
    known = model.bucket(utc_now, utc_now + timedelta(hours=1))
    unknown = model.bucket(utc_now - timedelta(hours=2), utc_now - timedelta(hours=1))
    assert known.burn == 0 and known.coverage == 1 and not known.uncertain
    assert unknown.burn is None and unknown.coverage == 0
    assert known.label == "0.0" and unknown.label == ""
    partial = CalendarModel(
        Snapshot("a", "a", "pro", {}, start + 3600, readings),
        utc_now + timedelta(hours=1),
    ).bucket(utc_now, utc_now + timedelta(hours=4))
    assert partial.partial and not partial.uncertain and partial.label == "0.0+"


def test_counter_rebase_does_not_count_a_refund_or_combine_pools(utc_now):
    start = utc_now.timestamp()
    reset = start + 86400
    readings = [
        observation(start + index * 300, used, reset)
        for index, used in enumerate([40, 45, 2, 3, 4])
    ]
    result = intervals(readings, "weekly_all", start + 2000)
    assert all(item.burn >= 0 for item in result)
    assert len({item.pool for item in result}) == 2
    model = CalendarModel(
        Snapshot("a", "a", "pro", {}, start + 2000, readings),
        utc_now + timedelta(hours=1),
    )
    assert model.bucket(utc_now, utc_now + timedelta(hours=1)).burn is None


def test_short_pressure_and_weekly_surplus_coexist(utc_tz):
    source = DemoSource()
    snapshot = source.load()[0]
    conditions, advice = evaluate(snapshot, source.now)
    assert {(c.meter, c.event) for c in conditions} >= {
        ("session", "pace"),
        ("weekly_all", "surplus"),
    }
    assert "~39% unused" in advice[0]
    assert "14:39" in next(c.message for c in conditions if c.meter == "session")


def test_forecast_stops_at_pool_reset_and_is_not_reused_for_fable(utc_tz):
    source = DemoSource()
    snapshot = source.load()[0]
    model = CalendarModel(snapshot, source.now)
    start = source.now + timedelta(hours=2)
    assert model.bucket(start, start + timedelta(hours=1)).forecast is not None
    reset = datetime.fromtimestamp(model.meter.reset, timezone.utc)
    assert model.bucket(reset, reset + timedelta(hours=1)).forecast is None
    scoped = CalendarModel(snapshot, source.now, "weekly_scoped:Fable")
    assert scoped.bucket(start, start + timedelta(hours=1)).forecast is None
    stale = CalendarModel(replace(snapshot, error="429"), source.now)
    assert stale.bucket(start, start + timedelta(hours=1)).forecast is None


def test_journal_dedupes_after_restart_and_stale_does_not_clear(
    tmp_path, monkeypatch, utc_tz
):
    sent = []
    monkeypatch.setattr(cc, "send_notification", lambda *args: sent.append(args))
    source = DemoSource("weekly")
    first = source.load()[0]
    path = tmp_path / "alerts.json"
    journal = AlertJournal(path)
    journal.update([first], source.now)
    assert len(sent) == 1
    restored = AlertJournal(path)
    restored.update([first], source.now)
    assert len(sent) == 1
    stale = replace(first, error="429", observed=first.observed + 100)
    restored.update([stale], source.now)
    assert len(sent) == 1
    assert any(s["active"] for s in restored.state["conditions"].values())
    restored.acknowledge(restored.events[0]["id"])
    assert json.loads(path.read_text())["events"][0]["acknowledged"]


def test_forecast_warning_needs_two_distinct_observations(utc_tz):
    source = DemoSource()
    first = source.load()[0]
    journal = AlertJournal()
    assert journal.update([first], source.now) == []
    assert journal.update([first], source.now) == []
    second = replace(first, observed=first.observed + 120)
    events = journal.update([second], source.now + timedelta(seconds=120))
    assert {e["event"] for e in events} == {"pace", "surplus"}
    assert all(e["data"]["condition_id"] and e["data"]["transition_id"] for e in events)


def test_timer_reset_is_not_a_recovery(utc_tz):
    source = DemoSource("weekly")
    snapshot = source.load()[0]
    journal = AlertJournal()
    journal.update([snapshot], source.now)
    count = len(journal.events)
    journal.update([snapshot], source.now + timedelta(days=2))
    assert len(journal.events) == count


def test_calendar_cli_demo_does_not_discover_credentials(monkeypatch):
    monkeypatch.setattr(cc.sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(cc.sys.stdout, "isatty", lambda: True)
    monkeypatch.setattr(
        cc, "get_all_credentials", lambda *a: pytest.fail("demo discovered credentials")
    )
    monkeypatch.setattr(CalendarApp, "run", lambda self: None)
    assert cc.main(["--calendar", "timeline", "--demo"]) == 0


def test_demo_rejects_network_info_flags(monkeypatch):
    monkeypatch.setattr(
        cc, "info_hello", lambda: pytest.fail("demo contacted provider")
    )
    with pytest.raises(SystemExit) as error:
        cc.main(["--demo", "--hello"])
    assert error.value.code == 2


def test_live_collector_uses_shared_core_and_does_not_log_cached_echoes(
    store, monkeypatch, utc_tz
):
    source = DemoSource()
    demo = source.load()[0]
    path = store / "work.credentials.json"
    profiles = {str(path): {"account": {"uuid": "acct-a"}, "organization": {}}}

    async def profile_fetch(_):
        return profiles

    monkeypatch.setattr(cc, "fetch_all_profiles_async", profile_fetch)
    monkeypatch.setattr(cc, "profile_with_credential_tier", lambda p, _: p)
    monkeypatch.setattr(
        cc,
        "fetch_or_use_cache",
        lambda *a: (
            {
                **demo.payload,
                "fetched_at": datetime.now(timezone.utc).timestamp(),
                "_from_shared_cache": True,
            },
            True,
            0,
        ),
    )
    monkeypatch.setattr(cc, "send_notification", lambda *a: None)
    live = LiveSource([(path, "synthetic-token")])
    snapshots = live.load()
    assert snapshots[0].uuid == "acct-a"
    assert not list(store.rglob("usage.jsonl"))


async def loaded(app, pilot):
    for _ in range(100):
        await pilot.pause(0.05)
        if app.snapshots and not app.loading:
            return
    pytest.fail(f"calendar did not load: {app.load_error}")


def test_calendar_navigation_and_resize(utc_tz):
    async def run():
        app = CalendarApp(DemoSource())
        async with app.run_test(size=(120, 36)) as pilot:
            await loaded(app, pilot)
            app.action_view("calendar")
            table = app.query_one("#table", DataTable)
            assert table.row_count == 4
            assert not app.query("#layout")
            for row in range(table.row_count):
                cells = table.get_row_at(row)
                assert all("?" not in str(cell) for cell in cells)
                assert all(not str(cell).strip() for cell in cells[4:])
            assert "\n" in str(table.get_row_at(0)[1])
            table.focus()
            await pilot.press("right")
            date, band = app.selected_date, app.band
            await pilot.press("r")
            await loaded(app, pilot)
            await pilot.pause()
            assert (app.selected_date, app.band) == (date, band)
            await pilot.press("enter")
            await pilot.pause()
            assert app.view == "day" and table.row_count == 24
            await pilot.press("escape", "2")
            await pilot.pause()
            assert app.view == "history" and table.row_count >= 3
            await pilot.press("enter")
            assert app.view == "day"
            await pilot.press("escape", "r")
            await loaded(app, pilot)
            await pilot.press("3")
            await pilot.pause()
            assert table.row_count >= 2
            await pilot.press("x")
            assert any(e.get("acknowledged") for e in app.source.journal.events)
            await pilot.press("1")
            await pilot.resize_terminal(80, 24)
            await pilot.pause()
            assert not app.query_one("#inspector").display
            assert app.query_one("#table").size.height >= 5
            await pilot.resize_terminal(50, 24)
            await pilot.pause()
            assert table.row_count == 7
            await pilot.resize_terminal(160, 48)
            await pilot.pause()
            assert app.query_one("#inspector").display
            await pilot.press("ctrl+t")
            await pilot.pause()
            assert app.theme == "ccpace-dark"
            await pilot.press("ctrl+t")
            await pilot.pause()
            assert app.theme == "ccpace-light"
            await pilot.press("m")
            await pilot.pause()
            assert app.metric == "weekly_scoped:Fable"
            await pilot.press("a")
            await pilot.pause()
            assert app.snapshot.account == "personal"
            assert app.metric == "weekly_all"

    asyncio.run(run())


def test_day_inspector_keeps_dst_fold(utc_tz, monkeypatch):
    tz = ZoneInfo("America/Los_Angeles")
    monkeypatch.setattr(cc, "DISPLAY_TZS", [(tz, "Los Angeles")])

    async def run():
        app = CalendarApp(DemoSource())
        async with app.run_test(size=(100, 36)) as pilot:
            await loaded(app, pilot)
            app.selected_date = datetime(2026, 11, 1).date()
            app.view = "day"
            app.draw_table()
            assert app.query_one("#table", DataTable).row_count == 25
            assert len({at.timestamp() for at in app.row_metadata}) == 25

    asyncio.run(run())


def test_queued_highlight_after_table_detaches(utc_tz):
    async def run():
        app = CalendarApp(DemoSource())
        async with app.run_test(size=(100, 36)) as pilot:
            await loaded(app, pilot)
            app.draw_table()
            await app.query_one("#table", DataTable).remove()
            app.draw_detail()
            await pilot.pause()

    asyncio.run(run())


@pytest.mark.parametrize("scenario", SCENARIOS)
def test_demo_scenarios_render_without_live_effects(scenario, store, monkeypatch):
    monkeypatch.setattr(
        cc, "send_notification", lambda *a: pytest.fail("demo sent notification")
    )
    monkeypatch.setattr(
        cc, "get_all_credentials", lambda *a: pytest.fail("demo read credentials")
    )

    async def run():
        app = CalendarApp(DemoSource(scenario))
        async with app.run_test(size=(80, 24)) as pilot:
            await loaded(app, pilot)
            assert not app.load_error
            app.action_view("calendar")
            assert app.query_one("#table", DataTable).row_count == 4
            for row in range(4):
                assert all(
                    "?" not in str(cell)
                    for cell in app.query_one("#table", DataTable).get_row_at(row)
                )
            app.selected_date = app.week + timedelta(days=6)
            app.draw_table()
            await pilot.pause()
            assert (
                app.unavailable_label(
                    app.midnight(app.selected_date),
                    app.model.bucket(
                        app.midnight(app.selected_date),
                        app.midnight(app.selected_date + timedelta(days=1)),
                    ),
                )
                == "Next quota period"
            )

    asyncio.run(run())
    assert not list(store.rglob("*.json"))


def test_calendar_forecast_honors_access_boundary(utc_tz):
    source = DemoSource()
    snapshot = source.load()[0]
    boundary = source.now + timedelta(hours=2)
    snapshot = replace(snapshot, access_end=boundary, access_note="trial end")
    model = CalendarModel(snapshot, source.now)
    assert model.bucket(boundary, boundary + timedelta(hours=1)).forecast is None
    conditions, advice = evaluate(snapshot, source.now)
    assert "at trial end" in advice[0]
    assert "~40% used" in advice[0]
    assert evaluate(snapshot, boundary)[0] == []


def test_pace_escalation_to_cap_is_not_recovery(utc_tz):
    source = DemoSource()
    first = source.load()[0]
    journal = AlertJournal()
    journal.update([first], source.now)
    journal.update(
        [replace(first, observed=first.observed + 60)],
        source.now + timedelta(seconds=60),
    )
    capped = replace(
        first,
        observed=first.observed + 120,
        payload={
            **first.payload,
            "five_hour": {**first.payload["five_hour"], "utilization": 100},
        },
    )
    events = journal.update([capped], source.now + timedelta(seconds=120))
    assert any(e["event"] == "full" for e in events)
    assert not any(e["data"]["phase"] == "cleared" for e in events)


def test_stale_ui_does_not_claim_exhaustion(utc_tz):
    async def run():
        app = CalendarApp(DemoSource("stale"))
        async with app.run_test(size=(80, 24)) as pilot:
            await loaded(app, pilot)
            text = app.query_one("#advice", Static).render()
            assert "exhausted" not in str(text)
            assert "Forecast unavailable" in str(text)

    asyncio.run(run())


def test_journal_failure_does_not_hide_live_data(store, monkeypatch):
    async def profiles(_):
        return {}

    monkeypatch.setattr(cc, "fetch_all_profiles_async", profiles)
    source = DemoSource()
    payload = {
        **source.load()[0].payload,
        "fetched_at": datetime.now(timezone.utc).timestamp(),
    }
    monkeypatch.setattr(cc, "fetch_or_use_cache", lambda *a: (payload, True, 0))
    monkeypatch.setattr(cc, "profile_with_credential_tier", lambda *a: {})
    live = LiveSource([(store / "work.credentials.json", "synthetic")], no_log=True)

    def fail(*a):
        raise OSError("read-only")

    monkeypatch.setattr(live.journal, "update", fail)
    assert live.load()[0].meters
