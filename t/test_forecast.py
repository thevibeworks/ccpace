"""The burn model, the walk, and the shared cache they both live in.

These three were wrong together and for the same reason: nobody could see
the model, only the sentence it produced, and the sentence was plausible.
The account that motivated this file was reading `+133% rest of week` on a
week with 56% left.
"""

from __future__ import annotations

import json
from datetime import timedelta

from conftest import (
    cc,
    five_entry,
    flat_profile,
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
    daily, r24, _ = cc.seven_day_envelope(rows, utc_now)
    assert sum(daily.values()) == 44
    assert r24 == 44


def test_envelope_first_sample_is_a_baseline(utc_now):
    """Seeing an account already at 40 is not watching it climb to 40."""
    base = utc_now.timestamp() - 3600
    daily, _, _ = cc.seven_day_envelope(
        [sample(base, 40), sample(base + 60, 45)], utc_now
    )
    assert sum(daily.values()) == 5


def test_envelope_confirmed_reset_rebaselines(utc_now):
    """A real reset sticks and is deep: hold at 90, fall to 2 and stay, then
    climb to 20. Credit the 18 after the reset, never the 90 again."""
    base = utc_now.timestamp() - 3600
    rows = [sample(base + i * 60, u) for i, u in enumerate([50, 90, 2, 3, 20])]
    daily, _, _ = cc.seven_day_envelope(rows, utc_now)
    # 40 up to 90, then 18 from the new baseline of 2
    assert sum(daily.values()) == 40 + 18


def test_envelope_shallow_dip_is_not_a_reset(utc_now):
    """Two samples down, but only by 5 points: too shallow to be a reset, so
    the envelope holds and the re-climb earns nothing."""
    base = utc_now.timestamp() - 3600
    rows = [sample(base + i * 60, u) for i, u in enumerate([50, 60, 55, 55, 60])]
    daily, _, _ = cc.seven_day_envelope(rows, utc_now)
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
    daily, _, _ = cc.seven_day_envelope(rows, utc_now)
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
    daily, _, _ = cc.seven_day_envelope(rows, utc_now)
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
