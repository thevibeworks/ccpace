"""Provider identity, optional windows, and read-only OAuth collection."""

import asyncio
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import html
import base64
import json
import ssl

import httpx
import pytest
from textual.widgets import DataTable
from textual.events import MouseScrollRight

from conftest import cc
from ccpace_calendar import (
    AlertJournal,
    CalendarModel,
    DemoSource,
    Snapshot,
    evaluate,
    meters,
)
from ccpace_providers import CodexSource, FleetSource, codex_meters, tls_context
from ccpace_tui import CalendarApp
from test_calendar import loaded


def payload(now, account="workspace-a"):
    return {
        "account_id": account,
        "email": "synthetic@example.invalid",
        "plan_type": "pro",
        "rate_limit": {
            "primary_window": {
                "used_percent": 31,
                "limit_window_seconds": 18000,
                "reset_at": now.timestamp() + 3600,
            },
            "secondary_window": {
                "used_percent": 64,
                "limit_window_seconds": 604800,
                "reset_at": now.timestamp() + 86400,
            },
        },
        "credits": {"balance": "25"},
    }


def auth_file(store, account="workspace-a", name="auth.work.json"):
    path = store / name
    path.write_text(
        json.dumps(
            {
                "tokens": {
                    "access_token": "synthetic-access-token",
                    "account_id": account,
                    "refresh_token": "synthetic-refresh-token",
                }
            }
        )
    )
    return path


def test_missing_reset_preserves_idle_and_unknown(utc_now):
    result = meters(
        {"five_hour": None, "seven_day": {"utilization": 0, "resets_at": None}}
    )
    assert len(result) == 2 and all(not m.timed for m in result)
    assert result[0].state == "inactive"
    snapshot = Snapshot(
        "work",
        "a",
        "pro",
        {"five_hour": {"utilization": 0, "resets_at": None}},
        utc_now.timestamp(),
    )
    assert snapshot.meters[0].used == 0 and snapshot.meters[0].reset is None
    assert evaluate(snapshot, utc_now)[0] == []
    assert (
        CalendarModel(snapshot, utc_now)
        .bucket(utc_now, utc_now + timedelta(hours=1))
        .forecast
        is None
    )


def test_codex_duration_mapping_handles_weekly_primary(utc_now):
    data = payload(utc_now)
    data["rate_limit"] = {
        "primary_window": data["rate_limit"]["secondary_window"],
        "secondary_window": None,
    }
    result = codex_meters(data)
    assert [(m.key, m.used) for m in result] == [("weekly_all", 64)]
    data["rate_limit"]["primary_window"]["reset_at"] = None
    data["rate_limit"]["primary_window"]["used_percent"] = 0
    assert codex_meters(data)[0].state == "inactive"


def test_codex_reads_are_scoped_cached_and_do_not_change_auth(store):
    now = datetime.now(timezone.utc)
    path = auth_file(store)
    original = path.read_bytes()
    calls = []

    def handler(request):
        calls.append(request)
        assert request.method == "GET"
        assert request.headers["ChatGPT-Account-Id"] == "workspace-a"
        if request.url.path.endswith("/usage"):
            return httpx.Response(200, json=payload(now))
        return httpx.Response(
            200,
            json={
                "available_count": 1,
                "credits": [
                    {"expires_at": now.timestamp() + 86400, "status": "available"}
                ],
            },
        )

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        source = CodexSource(path, client=client)
        snapshot = source.load()[0]
        assert not snapshot.error and snapshot.provider == "codex"
        assert snapshot.reset_inventory["available_count"] == 1
        assert source.load(True)[0].observed == snapshot.observed
    assert len(calls) == 2 and path.read_bytes() == original
    root = next((cc.data_root() / "providers/codex/accounts").iterdir())
    persisted = (root / "usage.cache").read_text() + (root / "usage.jsonl").read_text()
    assert (
        "synthetic-access-token" not in persisted
        and "synthetic-refresh-token" not in persisted
    )
    assert (
        "synthetic@example.invalid" not in persisted and "workspace-a" not in persisted
    )
    assert len((root / "usage.jsonl").read_text().splitlines()) == 1


def test_cached_codex_state_survives_auth_failure_after_restart(store):
    now = datetime.now(timezone.utc)
    path = auth_file(store)
    with httpx.Client(
        transport=httpx.MockTransport(
            lambda r: httpx.Response(
                200, json=payload(now) if r.url.path.endswith("/usage") else {}
            )
        )
    ) as client:
        CodexSource(path, client=client).load()
    cache_path = next((store / "providers").rglob("usage.cache"))
    cache = json.loads(cache_path.read_text())
    cache["timestamp"] -= 120
    cache_path.write_text(json.dumps(cache))
    with httpx.Client(
        transport=httpx.MockTransport(lambda r: httpx.Response(401))
    ) as client:
        snapshot = CodexSource(path, client=client).load()[0]
    assert snapshot.error and snapshot.observed == cache["timestamp"]
    assert snapshot.meters[0].used == 31


def test_account_mismatch_cannot_publish_another_pool(store):
    path = auth_file(store)
    with httpx.Client(
        transport=httpx.MockTransport(
            lambda r: httpx.Response(
                200, json=payload(datetime.now(timezone.utc), "workspace-b")
            )
        )
    ) as client:
        snapshot = CodexSource(path, client=client).load()[0]
    assert "does not match" in snapshot.error
    assert not list((store / "providers").rglob("usage.cache"))


def test_fleet_keeps_same_alias_from_different_providers(store):
    source = DemoSource()
    a = source.load()[0]
    b = replace(
        a, provider="codex", uuid="different", limits=codex_meters(payload(source.now))
    )

    class Stub:
        def __init__(self, snapshots):
            self.snapshots = snapshots

        def load(self, force=False):
            return self.snapshots

    fleet = FleetSource(
        [Stub([a]), Stub([b]), Stub([replace(a, source_id="other-file")])], notify=False
    )
    result = fleet.load()
    assert len(result) == 2
    assert (
        result[0].account == result[1].account
        and result[0].identity != result[1].identity
    )


def test_users_in_same_codex_workspace_keep_separate_histories(store):
    now = datetime.now(timezone.utc)
    identities = []
    with httpx.Client(
        transport=httpx.MockTransport(
            lambda r: httpx.Response(
                200, json=payload(now) if r.url.path.endswith("/usage") else {}
            )
        )
    ) as client:
        for user in ("user-a", "user-b"):
            path = auth_file(store, name=f"auth.{user}.json")
            auth = json.loads(path.read_text())
            encoded = (
                base64.urlsafe_b64encode(json.dumps({"sub": user}).encode())
                .decode()
                .rstrip("=")
            )
            auth["tokens"]["id_token"] = f"synthetic.{encoded}.signature"
            path.write_text(json.dumps(auth))
            identities.append(CodexSource(path, client=client).load()[0].identity)
    assert len(set(identities)) == 2
    assert len(list((store / "providers").rglob("usage.cache"))) == 2


def test_provider_events_never_share_identity(utc_tz):
    source = DemoSource("weekly")
    claude = source.load()[0]
    codex = replace(claude, provider="codex")
    assert evaluate(claude, source.now)[0][0].id != evaluate(codex, source.now)[0][0].id
    journal = AlertJournal()
    events = journal.update([claude, codex], source.now)
    assert {e["data"]["provider"] for e in events} == {"claude", "codex"}


def test_codex_429_respects_cooldown_even_on_manual_refresh(store):
    path = auth_file(store)
    calls = []

    def limited(request):
        calls.append(request)
        return httpx.Response(429, headers={"Retry-After": "3600"})

    with httpx.Client(transport=httpx.MockTransport(limited)) as client:
        source = CodexSource(path, client=client)
        assert source.load()[0].error
        assert source.load(True)[0].error
    assert len(calls) == 1


def test_tls_client_still_verifies_chain_and_hostname(monkeypatch):
    for name in ("SSL_CERT_FILE", "SSL_CERT_DIR", "NODE_EXTRA_CA_CERTS"):
        monkeypatch.delenv(name, raising=False)
    context = tls_context()
    assert context.verify_mode == ssl.CERT_REQUIRED and context.check_hostname


def test_account_overview_mouse_and_tab_keys(utc_tz):
    async def run():
        app = CalendarApp(DemoSource())
        async with app.run_test(size=(120, 36)) as pilot:
            await loaded(app, pilot)
            assert app.view == "accounts"
            table = app.query_one("#table", DataTable)
            assert table.row_count == 4
            table.move_cursor(row=2)
            await pilot.pause()
            assert app.snapshot.provider == "codex"
            await pilot.press("enter")
            await pilot.pause()
            assert app.view == "calendar" and app.snapshot.provider == "codex"
            assert "00:00" in html.unescape(app.export_screenshot()).replace(
                "\u00a0", " "
            )
            before = app.week
            for _ in range(3):
                table.post_message(
                    MouseScrollRight(table, 1, 1, 1, 0, 0, False, False, False)
                )
            await pilot.pause()
            assert app.week == before + timedelta(days=7)
            await pilot.press("r")
            await loaded(app, pilot)
            assert app.snapshot.provider == "codex"
            await pilot.click("#table", offset=(12, 2))
            await pilot.pause()
            assert app.view == "calendar"
            await pilot.click("#table", offset=(12, 0))
            await pilot.pause()
            assert app.whole_day
            await pilot.click("#table", offset=(12, 2))
            await pilot.pause()
            assert app.view == "calendar"
            await pilot.click("#table", offset=(12, 2), times=2)
            await pilot.pause()
            assert app.view == "day"
            await pilot.press("escape", "ctrl+pagedown")
            await pilot.pause()
            assert app.view == "history"
            await pilot.press("0")
            await pilot.pause()
            assert app.view == "accounts"

    asyncio.run(run())


def test_default_interactive_and_watch_open_calendar(monkeypatch):
    monkeypatch.setattr(cc.sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(cc.sys.stdout, "isatty", lambda: True)
    opened = []
    monkeypatch.setattr(CalendarApp, "run", lambda self: opened.append(self.source))
    import ccpace_providers

    source = DemoSource()
    source.sources = [object()]
    monkeypatch.setattr(ccpace_providers, "build_source", lambda *a: source)
    assert cc.main([]) == 0
    assert cc.main(["--watch"]) == 0
    assert len(opened) == 2


def test_no_active_window_is_visible_and_clock_axis_is_independent(utc_tz):
    async def run():
        app = CalendarApp(DemoSource())
        async with app.run_test(size=(80, 24)) as pilot:
            await loaded(app, pilot)
            app.query_one("#table", DataTable).move_cursor(row=3)
            await pilot.pause()
            await pilot.press("enter")
            await pilot.pause()
            assert "No active window" in html.unescape(app.export_screenshot()).replace(
                "\u00a0", " "
            )
            labels = [
                str(app.query_one("#table", DataTable).get_row_at(i)[0])
                for i in range(4)
            ]
            assert labels == ["00:00", "06:00", "12:00", "18:00"]

    asyncio.run(run())


def test_noninteractive_json_does_not_enter_tui(monkeypatch, capsys):
    monkeypatch.setattr(cc.sys.stdin, "isatty", lambda: False)
    monkeypatch.setattr(cc.sys.stdout, "isatty", lambda: False)
    monkeypatch.setattr(CalendarApp, "run", lambda self: pytest.fail("started TUI"))
    assert cc.main(["--demo", "--json"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["schema"] == 1
    assert {a["provider"] for a in result["accounts"]} == {"claude", "codex"}
