"""The burn model, the walk, and the shared cache they both live in.

These three were wrong together and for the same reason: nobody could see
the model, only the sentence it produced, and the sentence was plausible.
The account that motivated this file was reading `+133% rest of week` on a
week with 56% left.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest
from conftest import (
    cc,
    five_entry,
    flat_profile,
    hour_shape,
    sample,
    seven_entry,
    write_cache,
)

DAY = 86400


# --- the envelope --------------------------------------------------------

def test_envelope_credits_the_rise_not_the_deltas(utc_now):
    """10 -> 30 -> 50, one stale sample at 4, then 54.

    Real burn is 44. Summing raw positive deltas reads 90: the dip is
    refunded and then re-earned. This is the arithmetic that put 149%/day
    into a Thursday.
    """
    base = utc_now.timestamp() - 3600
    rows = [sample(base + i * 60, u) for i, u in enumerate([10, 30, 50, 4, 54])]
    daily, r24, _, _ = cc.seven_day_envelope(rows, utc_now)
    assert sum(daily.values()) == 44
    assert r24 == 44


def test_envelope_first_sample_is_a_baseline(utc_now):
    """Seeing an account already at 40 is not watching it climb to 40."""
    base = utc_now.timestamp() - 3600
    daily, _, _, _ = cc.seven_day_envelope(
        [sample(base, 40), sample(base + 60, 45)], utc_now
    )
    assert sum(daily.values()) == 5


def test_envelope_confirmed_reset_rebaselines(utc_now):
    """A real reset sticks and is deep: hold at 90, fall to 2 and stay, then
    climb to 20. Credit the 18 after the reset, never the 90 again."""
    base = utc_now.timestamp() - 3600
    rows = [sample(base + i * 60, u) for i, u in enumerate([50, 90, 2, 3, 20])]
    daily, _, _, _ = cc.seven_day_envelope(rows, utc_now)
    # 40 up to 90, then 18 from the new baseline of 2
    assert sum(daily.values()) == 40 + 18


def test_envelope_shallow_dip_is_not_a_reset(utc_now):
    """Two samples down, but only by 5 points: too shallow to be a reset, so
    the envelope holds and the re-climb earns nothing."""
    base = utc_now.timestamp() - 3600
    rows = [sample(base + i * 60, u) for i, u in enumerate([50, 60, 55, 55, 60])]
    daily, _, _, _ = cc.seven_day_envelope(rows, utc_now)
    assert sum(daily.values()) == 10


def test_envelope_drops_a_stale_session_reporting_an_old_window(utc_now):
    """A session that sat idle across a reset reports the window it last saw.

    Its key is older than the live one, and its numbers are the old window's.
    Held against the new envelope they read as a 60-point fall; the key says
    they are not this window's business at all.
    """
    base = utc_now.timestamp() - 3600
    new_key, old_key = base + 10000, base - 600000
    rows = [
        sample(base, 10, seven_reset=new_key),
        sample(base + 60, 70, seven_reset=new_key),
        sample(base + 120, 65, seven_reset=old_key),   # stale, wrong window
        sample(base + 180, 62, seven_reset=old_key),
        sample(base + 240, 75, seven_reset=new_key),
    ]
    daily, _, _, _ = cc.seven_day_envelope(rows, utc_now)
    assert sum(daily.values()) == 65  # 10 -> 75 in the live window, nothing else


def test_envelope_newer_window_key_rebaselines_immediately(utc_now):
    """A newer key IS certainly a new window — no two-signal wait needed."""
    base = utc_now.timestamp() - 3600
    rows = [
        sample(base, 10, seven_reset=1000),
        sample(base + 60, 95, seven_reset=1000),
        sample(base + 120, 3, seven_reset=99999),   # new window, new baseline
        sample(base + 180, 9, seven_reset=99999),
    ]
    daily, _, _, _ = cc.seven_day_envelope(rows, utc_now)
    assert sum(daily.values()) == 85 + 6


def _three_weeks(now, rate: float, uuid: str = "acct-A") -> list:
    """Three 7d windows of a counter that climbs `rate` a day and resets at
    each week boundary — what the series actually looks like, rather than a
    sawtooth no real account produces."""
    rows = []
    for week in range(3):
        key = now.timestamp() - (20 - week * 7) * DAY
        for day in range(7):
            start = key + day * DAY
            base = rate * day
            rows.append(sample(start, base, uuid=uuid, seven_reset=key))
            rows.append(sample(start + 14400, base + rate, uuid=uuid, seven_reset=key))
    return rows


def test_weekday_profile_partitions_by_account(utc_now):
    """usage.jsonl interleaves accounts and mixing them produces garbage burn
    rates — the observed failure was 9000%/day."""
    rows = _three_weeks(utc_now, 20) + _three_weeks(utc_now, 90, uuid="acct-B")
    got = cc.weekday_burn_forecast(rows, "acct-A", utc_now)
    assert got is not None
    assert all(19 <= v <= 21 for v in got["weekday_profile"].values())
    assert got["schema"] == cc.FORECAST_SCHEMA


def test_weekday_profile_excludes_today(utc_now):
    """Today is partial: training on it teaches the model that this weekday
    is quiet, every day, forever."""
    rows = [sample(utc_now.timestamp() - 60, 10), sample(utc_now.timestamp() - 30, 40)]
    # and it must publish NOTHING rather than an all -1 profile: this cache is
    # shared, and an empty one stamped `now` silences every surface reading it
    assert cc.weekday_burn_forecast(rows, "acct-A", utc_now) is None


# --- the shape of a day --------------------------------------------------

def _shaped_days(now, hours, *, days: int = 21, per_hour: float = 2.0,
                 uuid: str = "acct-A") -> list:
    """A counter that climbs `per_hour` in each local hour of `hours`, every
    day for `days` days back, resetting at each 7d window boundary. `hours`
    may be a callable of days-back, for a rhythm that moved. Nothing lands on
    today — the builder would refuse to train on it anyway."""
    hours_on = hours if callable(hours) else (lambda back: hours)
    rows, week, util = [], None, 0.0
    midnight = now.astimezone(timezone.utc).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    for back in range(days, 0, -1):
        day = midnight - timedelta(days=back)
        key = int(day.timestamp() // (7 * DAY)) * (7 * DAY)
        if key != week:
            week, util = key, 0.0
            rows.append(sample(day.timestamp(), 0.0, uuid=uuid, seven_reset=key))
        for hour in hours_on(back):
            util += per_hour
            rows.append(sample(
                day.timestamp() + hour * 3600 + 1800, util, uuid=uuid, seven_reset=key
            ))
    return rows


def test_hour_profile_is_a_share_of_the_day_floored_and_renormalized(utc_tz, utc_now):
    """Nine working hours carry the whole day's burn.

    Each is 1/9 of it, so 24/9 = 2.67 before the floor lifts the fifteen
    idle hours off zero; that lift is paid for by scaling the whole shape
    back to mean 1. The ORDER is the point: floored after renormalizing, a
    rest hour would read exactly 0.10.
    """
    rows = _shaped_days(utc_now, range(9, 18))
    got = cc.weekday_burn_forecast(rows, "acct-A", utc_now)
    shape = got["hour_profile"]
    assert sorted(shape) == sorted(str(h) for h in range(24))
    assert shape["12"] == 2.51                      # (24/9) * 24/25.5
    assert shape["3"] == 0.09                       # the floor, scaled
    assert 0.9 <= sum(shape.values()) / 24 <= 1.1
    assert cc.hour_multipliers(got) is not None


def test_hour_profile_never_trains_on_today(utc_tz, utc_now):
    """Today is partial for the hours exactly as it is for the day: a 03:00
    burst this morning is not evidence that this account works nights."""
    rows = _shaped_days(utc_now, range(9, 18))
    quiet = cc.weekday_burn_forecast(rows, "acct-A", utc_now)["hour_profile"]

    last = rows[-1]
    today = utc_now.astimezone(timezone.utc).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    rows.append(sample(today.timestamp() + 3 * 3600, last.util + 40,
                       seven_reset=last.seven_reset))
    assert cc.weekday_burn_forecast(rows, "acct-A", utc_now)["hour_profile"] == quiet


def test_hour_profile_lets_an_old_rhythm_fade(utc_tz, utc_now):
    """Same 14-day half-life the weekdays use: a schedule you moved off three
    weeks ago must not still be shaping tonight's forecast."""
    rows = _shaped_days(utc_now, lambda back: [9] if back > 14 else [21])
    shape = cc.weekday_burn_forecast(rows, "acct-A", utc_now)["hour_profile"]
    assert shape["21"] > shape["9"] > cc.REST_MULT_MAX


def test_hour_profile_is_read_only_when_it_is_the_shape_it_claims(utc_tz):
    """A co-writer's dialect, a truncated write, a value off the scale: the
    reader takes flat over any of them, and never goes silent over one."""
    good = hour_shape(range(8))
    assert cc.hour_multipliers({"hour_profile": good})[0] == 0.1
    assert cc.hour_multipliers(None) is None
    assert cc.hour_multipliers({}) is None
    assert cc.hour_multipliers({"hour_profile": {"0": 1.0}}) is None       # 23 short
    assert cc.hour_multipliers({"hour_profile": {str(h): 3.0 for h in range(24)}}) is None
    assert cc.hour_multipliers({"hour_profile": dict(good, **{"5": 30})}) is None
    assert cc.hour_multipliers({"hour_profile": dict(good, **{"5": "x"})}) is None


# --- the walk ------------------------------------------------------------

def test_landing_is_capped_at_the_pool(utc_now):
    """The bug that started this: a pattern that outruns the pool used to
    print `lands ~177%`. Utilization cannot exceed the pool — above 100 the
    honest answer is a DATE, and that is what the second element is."""
    entry = seven_entry(44, 2 * DAY, utc_now)
    landing, dry = cc.project_week(flat_profile(60), entry, utc_now)
    assert landing == 100
    assert dry is not None and dry < entry["reset_dt"]


def test_landing_under_the_pool_reports_no_wall(utc_now):
    entry = seven_entry(44, 2 * DAY, utc_now)
    landing, dry = cc.project_week(flat_profile(10), entry, utc_now)
    assert 60 <= landing <= 68     # 44 + ~2 days at 10%/day
    assert dry is None


def test_recent_burn_blends_over_the_first_day(utc_now):
    """A hot streak escalates before the weekday average catches up: the
    first 24h burns at max(profile, recent_24h)."""
    entry = seven_entry(20, 2 * DAY, utc_now)
    cold = cc.project_week(flat_profile(10), entry, utc_now)[0]
    hot = cc.project_week(flat_profile(10, recent_24h=40), entry, utc_now)[0]
    assert hot > cold


def test_walk_is_silent_on_a_corrupt_profile(utc_now):
    """No weekday can average more than the whole pool per day. A profile
    that claims one came from a broken accountant; corrupt input earns
    silence, not a forecast."""
    entry = seven_entry(44, 2 * DAY, utc_now)
    bad = flat_profile(10)
    bad["weekday_profile"]["3"] = 149.11
    assert cc.project_week(bad, entry, utc_now) is None


def test_walk_is_silent_on_a_young_window(utc_now):
    """Minutes after a rollover the profile describes the windows BEFORE
    this one, and the 24h blend describes a day across the reset."""
    entry = seven_entry(2, cc.WINDOW_7D_SEC - 3600, utc_now)
    assert cc.project_week(flat_profile(10), entry, utc_now) is None


def test_walk_is_silent_on_a_cold_start(utc_now):
    entry = seven_entry(44, 2 * DAY, utc_now)
    assert cc.project_week(flat_profile(10, days=3), entry, utc_now) is None
    assert cc.project_week(None, entry, utc_now) is None


def test_walk_without_an_hour_shape_is_the_day_walk(utc_tz, utc_now):
    """The regression the shaped walk owes the old one. Stepping by local hour
    instead of by local day may not move a single number when there is no
    shape to apply: two days at 10%/day is 20 points, and a pool with 56 left
    at 60%/day dries 22h24m out, hour walk or day walk."""
    entry = seven_entry(44, 2 * DAY, utc_now)
    assert cc.project_week(flat_profile(10), entry, utc_now)[0] == pytest.approx(64)

    landing, dry = cc.project_week(flat_profile(60), entry, utc_now)
    assert landing == 100
    assert abs((dry - (utc_now + timedelta(hours=22.4))).total_seconds()) < 1


def test_a_corrupt_hour_shape_is_flat_and_never_silence(utc_tz, utc_now):
    """The weekday guards decide whether a forecast exists; a bad hour shape
    only decides whether it knows when you sleep."""
    entry = seven_entry(44, 2 * DAY, utc_now)
    plain = cc.project_week(flat_profile(60), entry, utc_now)
    for broken in ({"0": 1.0}, {str(h): 3.0 for h in range(24)},
                   dict(hour_shape(range(8)), **{"5": 30})):
        bad = flat_profile(60)
        bad["hour_profile"] = broken
        assert cc.project_week(bad, entry, utc_now) == plain


def test_the_shape_moves_the_burn_without_moving_the_day(utc_tz, utc_now):
    """The multipliers have mean 1, so a whole day burns the weekday total
    whatever shape it has. What moves is WHEN inside the day."""
    entry = seven_entry(20, 2 * DAY, utc_now)
    shaped = flat_profile(10)
    shaped["hour_profile"] = hour_shape(range(8))
    assert cc.project_week(shaped, entry, utc_now)[0] == pytest.approx(
        cc.project_week(flat_profile(10), entry, utc_now)[0]
    )


def test_the_shape_moves_a_dry_out_of_the_night(utc_tz, utc_now):
    """The early-warning fix, and the whole point of the model.

    11pm, 10 points left, 60%/day: the flat walk puts the wall at 03:00 —
    a false alarm to the reader still awake and a missed warning to the one
    who reads it at breakfast. The account does not burn between midnight
    and 08:00, so the wall is really the next morning.
    """
    late = utc_now.replace(hour=23)
    entry = seven_entry(90, 2 * DAY, late)
    flat_dry = cc.project_week(flat_profile(60), entry, late)[1]
    assert flat_dry.astimezone(utc_tz).hour == 3

    shaped = flat_profile(60)
    shaped["hour_profile"] = hour_shape(range(8))
    dry = cc.project_week(shaped, entry, late)[1].astimezone(utc_tz)
    assert dry.hour >= 8 and dry.date() == flat_dry.astimezone(utc_tz).date()


def test_a_day_the_clock_shortens_burns_a_shorter_day(monkeypatch, utc_now):
    """A spring-forward day is 23 hours long and burns 23 hours of quota.

    The day walk sized its segments by subtracting two datetimes that shared
    one tzinfo, and Python does that on the WALL clock — so the hour the
    clock skipped was credited anyway, twice a year, in every zone that
    moves. Hour segments are measured in absolute seconds off the local
    clock's own minute, so the day is the length it actually is.
    """
    monkeypatch.setattr(cc, "DISPLAY_TZS", [(ZoneInfo("America/New_York"), "NY")])
    start = datetime(2026, 3, 8, 5, 0, tzinfo=timezone.utc)       # 00:00 EST
    entry = seven_entry(10, 23 * 3600, start)                     # to 00:00 EDT
    assert cc.project_week(flat_profile(24), entry, start)[0] == pytest.approx(33)


def test_walk_stops_at_the_access_boundary(utc_now):
    """Windows you cannot spend are not budget: one horizon per block."""
    entry = seven_entry(20, 4 * DAY, utc_now)
    full = cc.project_week(flat_profile(10), entry, utc_now)[0]
    short = cc.project_week(
        flat_profile(10), entry, utc_now, utc_now + timedelta(days=1)
    )[0]
    assert short < full


# --- windows ahead -------------------------------------------------------

def test_windows_ahead_excludes_the_one_you_are_in(utc_now):
    """The window you are standing in is where you are, not what you have
    left: it is already drawn as ▮ and already priced by the 5h row."""
    seven = seven_entry(50, 10 * 3600, utc_now)
    five = five_entry(10, 3600, utc_now)
    # 10h of week, 1h of window: 9h ahead once this one closes -> 2 windows
    assert cc.windows_ahead(seven, five) == 2
    # with no live 5h window there is nothing to exclude
    assert cc.windows_ahead(seven, None) == 2


def test_windows_ahead_holds_still_inside_a_window(utc_now):
    """Both clocks tick down together, so the difference does not move. The
    count is a countdown, not a reading that drifts mid-window."""
    counts = {
        cc.windows_ahead(
            seven_entry(50, 40 * 3600 - t, utc_now),
            five_entry(10, 4 * 3600 - t, utc_now),
        )
        for t in range(0, 4 * 3600, 600)
    }
    assert len(counts) == 1


def test_windows_ahead_is_zero_in_the_last_window(utc_now):
    """The week ends inside the window you are in: nothing is ahead of it."""
    seven = seven_entry(80, 3 * 3600, utc_now)
    five = five_entry(20, 3.5 * 3600, utc_now)
    assert cc.windows_ahead(seven, five) == 0


def test_awake_windows_counts_only_the_hours_you_are_up(utc_tz, utc_now):
    """Wed 12:00 to Fri 09:00 with the nights out: 12 + 16 + 1 = 29 waking
    hours, which is five windows and change, and change still buys work."""
    mult = [v for _, v in sorted(
        hour_shape(range(8)).items(), key=lambda kv: int(kv[0])
    )]
    assert cc.awake_windows(mult, utc_now, 3 * 3600, 48 * 3600) == 6
    # no live 5h window: the span starts now, and 3 more waking hours fit
    assert cc.awake_windows(mult, utc_now, 0, 48 * 3600) == 7
    # a 5h window outlasting the week leaves no span at all
    assert cc.awake_windows(mult, utc_now, 48 * 3600, 3 * 3600) == 0
    # a span that lies entirely in the night is not runway
    assert cc.awake_windows(mult, utc_now, 16 * 3600, 22 * 3600) == 0
    # and a 24/7 account is awake for every window in the span
    assert cc.awake_windows([1.0] * 24, utc_now, 3 * 3600, 48 * 3600) == 9


# --- the shared cache ----------------------------------------------------

def test_write_preserves_keys_it_does_not_compute(store, utc_now):
    """statusline computes a superset off the same log. Writing this dict
    flat over the file dropped its exchange rate, per-model profile and
    price join every time ccpace ran."""
    write_cache(store, {
        "schema": cc.FORECAST_SCHEMA,
        "computed_at": 0,
        "days_history": 20,
        "pct_per_window": 11.4,
        "scoped_name": "Fable",
        "scoped_profile": {"0": 5.0},
        "cost": {"usd_per_pct": 32.6},
        "weekday_profile": {"0": 1.0},
    })
    cc.write_forecast_cache("", {
        "schema": cc.FORECAST_SCHEMA,
        "computed_at": int(utc_now.timestamp()),
        "days_history": 26,
        "recent_24h": 5.0,
        "recent_48h": 9.0,
        "weekday_profile": {"0": 7.0},
    })
    got = json.loads((store / "forecast.cache").read_text())
    assert got["pct_per_window"] == 11.4
    assert got["scoped_name"] == "Fable"
    assert got["cost"]["usd_per_pct"] == 32.6
    assert got["weekday_profile"] == {"0": 7.0}     # ours, updated
    assert got["days_history"] == 26


def test_write_yields_to_a_fresh_cache_of_our_own_schema(store, utc_now):
    write_cache(store, {
        "schema": cc.FORECAST_SCHEMA,
        "computed_at": int(utc_now.timestamp()) - 60,
        "days_history": 20,
        "weekday_profile": {"0": 1.0},
    })
    cc.write_forecast_cache("", {
        "schema": cc.FORECAST_SCHEMA,
        "computed_at": int(utc_now.timestamp()),
        "days_history": 99,
        "weekday_profile": {"0": 7.0},
    })
    got = json.loads((store / "forecast.cache").read_text())
    assert got["days_history"] == 20     # the incumbent held


def test_write_replaces_a_cache_of_a_foreign_shape(store, utc_now):
    """Freshness is necessary and not sufficient. An unversioned cache is one
    a writer of another model left behind, however recently."""
    write_cache(store, {
        "computed_at": int(utc_now.timestamp()),
        "days_history": 251,
        "weekday_profile": {"3": 149.11},
    })
    cc.write_forecast_cache("", {
        "schema": cc.FORECAST_SCHEMA,
        "computed_at": int(utc_now.timestamp()),
        "days_history": 26,
        "weekday_profile": {"3": 26.7},
    })
    got = json.loads((store / "forecast.cache").read_text())
    assert got["schema"] == cc.FORECAST_SCHEMA
    assert got["weekday_profile"] == {"3": 26.7}


def test_read_accepts_only_a_fresh_cache_of_our_own_schema(store, utc_now):
    fresh = int(cc.datetime.now(cc.timezone.utc).timestamp())
    write_cache(store, {"schema": cc.FORECAST_SCHEMA, "computed_at": fresh,
                        "days_history": 20, "weekday_profile": {"0": 1.0}})
    assert cc.read_forecast_cache("")["days_history"] == 20

    write_cache(store, {"computed_at": fresh, "days_history": 20,
                        "weekday_profile": {"0": 1.0}})
    assert cc.read_forecast_cache("") is None          # no schema

    write_cache(store, {"schema": cc.FORECAST_SCHEMA,
                        "computed_at": fresh - cc.FORECAST_REBUILD_SEC - 1,
                        "days_history": 20, "weekday_profile": {"0": 1.0}})
    assert cc.read_forecast_cache("") is None          # stale

    (store / "forecast.cache").write_text("{not json")
    assert cc.read_forecast_cache("") is None


def test_the_hour_shape_survives_the_shared_cache(store, utc_tz, utc_now):
    """The field is only useful if it comes back out the way it went in:
    JSON keys are strings, and a reader that re-derives or re-rounds it is a
    third opinion about one week. Round-trip, and read what the co-writer
    reader reads."""
    write_cache(store, {"schema": cc.FORECAST_SCHEMA, "computed_at": 0,
                        "pct_per_window": 11.4})     # a key we do not compute
    built = cc.weekday_burn_forecast(
        _shaped_days(utc_now, range(9, 18)), "acct-A", utc_now
    )
    built["computed_at"] = int(cc.datetime.now(cc.timezone.utc).timestamp())
    cc.write_forecast_cache("", built)

    back = cc.read_forecast_cache("")
    assert back["pct_per_window"] == 11.4
    assert back["hour_profile"] == built["hour_profile"]
    assert cc.hour_multipliers(back)[12] == 2.51


def test_history_drops_uuidless_rows_and_counts_them(store, utc_now):
    """A row without a uuid is loss dressed as filtering unless the drop is
    counted: readers used to discard identifiable observations silently."""
    root = store
    (root / "accounts" / "work").mkdir(parents=True)
    ts = int(utc_now.timestamp())
    reset = utc_now.isoformat()

    def row(uuid, t):
        return json.dumps({
            "type": "usage", "timestamp": t,
            "user": {"uuid": uuid} if uuid else {},
            "five_hour": {"utilization": 1, "resets_at": reset},
            "seven_day": {"utilization": 5, "resets_at": reset},
        })

    (root / "usage.jsonl").write_text(
        "\n".join([row("acct-A", ts - 30), row("", ts - 20), row("acct-B", ts - 10)]) + "\n"
    )
    (root / "accounts" / "work" / "usage.jsonl").write_text(row("acct-A", ts) + "\n")

    rows, corpus = cc.load_account_corpus("work", "acct-A")
    assert [r.ts for r in rows] == [ts - 30, ts]  # both dirs, one account
    assert corpus.files == 2
    assert corpus.samples == 2
    assert corpus.dropped_no_uuid == 1
    assert corpus.dropped_other == 1
    assert corpus.oldest == ts - 30
    assert corpus.stamp()["uuid"] == "acct-A"


def test_weekday_profile_applies_one_uuid_rule(utc_now):
    """Unlabeled rows do not slip into a uuid-partitioned forecast: the loader
    drops them, and the forecast must not disagree with the loader."""
    rows = _three_weeks(utc_now, 20) + _three_weeks(utc_now, 90, uuid="")
    got = cc.weekday_burn_forecast(rows, "acct-A", utc_now)
    assert got is not None
    assert all(19 <= v <= 21 for v in got["weekday_profile"].values())
