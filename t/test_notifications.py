"""Notification contracts: the message and machine payload tell one story."""

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from conftest import cc, five_entry, seven_entry


def test_spent_row_says_cap_not_101_percent(capsys):
    cc.print_window_line("5h", 101, False)
    row = capsys.readouterr().out
    assert " cap " in row
    assert "101%" not in row


def test_threshold_payload_names_the_binding_window(monkeypatch):
    now = datetime.now(timezone.utc)
    reset_5h = (now + timedelta(hours=2)).isoformat()
    reset_7d = (now + timedelta(days=3)).isoformat()
    data = {
        "five_hour": {"utilization": 86, "resets_at": reset_5h},
        "seven_day": {"utilization": 53, "resets_at": reset_7d},
    }
    sent = []
    monkeypatch.setattr(
        cc,
        "send_notification",
        lambda event, account, payload, notifier: sent.append(
            (event, account, payload)
        ),
    )

    messages = cc.handle_notifications(
        "work.credentials.json", 86, 80, {}, {}, None, data
    )

    event, account, payload = sent[0]
    assert (event, account) == ("threshold", "work")
    assert payload["window"] == "5h"
    assert payload["utilization"] == 86
    assert payload["weekly_headroom"] == 47
    assert payload["reset_at"] == reset_5h
    assert cc.format_notification_message(event, account, payload).startswith(
        "work: 5h 86% · threshold 80% · back "
    )
    assert messages == [("threshold", "work", payload)]


def test_full_session_message_keeps_weekly_headroom_visible(monkeypatch):
    now = datetime.now(timezone.utc)
    data = {
        "five_hour": {
            "utilization": 101,
            "resets_at": (now + timedelta(hours=2)).isoformat(),
        },
        "seven_day": {
            "utilization": 53,
            "resets_at": (now + timedelta(days=3)).isoformat(),
        },
    }
    sent = []
    monkeypatch.setattr(
        cc,
        "send_notification",
        lambda event, account, payload, notifier: sent.append(
            (event, account, payload)
        ),
    )

    cc.handle_notifications("work.credentials.json", 101, 80, {}, {}, None, data)

    event, account, payload = next(item for item in sent if item[0] == "full")
    assert cc.format_notification_message(event, account, payload).startswith(
        "work: 5h capped · 47% of 7d left · back "
    )
    assert cc.notification_event_id(event, account, payload).startswith("full:work:5h:")


def test_delta_producer_and_formatter_use_the_same_keys(monkeypatch):
    now = datetime.now(timezone.utc)
    reset = (now + timedelta(hours=2)).isoformat()
    data = {
        "five_hour": {"utilization": 86, "resets_at": reset},
        "seven_day": {"utilization": 53},
    }
    previous = {"five_hour": {"utilization": 80}, "seven_day": {"utilization": 52}}
    sent = []
    monkeypatch.setattr(
        cc,
        "send_notification",
        lambda event, account, payload, notifier: sent.append(
            (event, account, payload)
        ),
    )

    cc.handle_delta_notification("work.credentials.json", data, previous, None)

    event, account, payload = sent[0]
    assert payload["window"] == "5h"
    assert payload["utilization"] == 86
    assert payload["delta"] == 6
    assert cc.format_notification_message(event, account, payload).startswith(
        "work: 5h 86% (+6%) · back "
    )
    assert cc.notification_event_id(event, account, payload).endswith(":86")


def test_custom_notifier_envelope_carries_the_event_id(monkeypatch):
    delivered = []
    monkeypatch.setattr(cc, "NOTIFY_CHANNELS", {})
    monkeypatch.setattr(cc, "validate_executable", lambda _: Path("/notifier"))
    monkeypatch.setattr(
        cc,
        "run_notifier",
        lambda args, input_data, timeout: (
            delivered.append(json.loads(input_data)) or True
        ),
    )
    data = {
        "window": "5h",
        "utilization": 100,
        "reset_at": "2026-09-02T11:00:00+00:00",
    }

    cc._send_notification("full", "work", data, "/notifier")

    assert delivered[0]["id"] == "full:work:5h:2026-09-02T11:00:00+00:00"
    assert delivered[0]["data"] == data


def test_capped_advice_distinguishes_session_from_week(utc_now):
    five = five_entry(100, 2 * 3600, utc_now)
    seven = seven_entry(53, 3 * 86400, utc_now)

    advice = cc.build_advice({}, [five, seven], now=utc_now)

    assert advice[0][0] == "warn"
    assert advice[0][1].startswith("5h capped · 47% of 7d left · back @")
