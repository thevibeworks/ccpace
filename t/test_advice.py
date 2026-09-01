"""The sentences. One model per block, and every number named for what it is.

What this file exists to prevent: a budget line saying `heading ~61%` off
linear pace with a forecast line under it saying `lands ~177%` off a
different model — same week, same breath, three-fold apart, and no way for
the reader to tell which one was the forecast.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from datetime import datetime, timedelta, timezone

import pytest
from conftest import cc, five_entry, flat_profile, hour_shape, scoped_entry, seven_entry

DAY = 86400


def advice(now, windows, **kw) -> list[tuple[str, str]]:
    return cc.build_advice(kw.pop("data", {}), windows, now=now, **kw)


def budget_of(lines: list[tuple[str, str]]) -> str:
    return next(msg for level, msg in lines if msg.startswith("budget:"))


def mix_of(lines: list[tuple[str, str]]) -> str | None:
    return next((msg for _, msg in lines if "at this mix" in msg), None)


def warns_of(lines: list[tuple[str, str]]) -> list[str]:
    return [msg for level, msg in lines if level == "warn"]


# --- the landing ---------------------------------------------------------

def test_budget_states_the_landing_and_names_the_model(utc_now):
    seven = seven_entry(44, 2 * DAY, utc_now)
    five = five_entry(10, 3600, utc_now)
    projection = cc.project_week(flat_profile(10), seven, utc_now)
    line = budget_of(advice(utc_now, [five, seven], projection=projection))
    assert "lands ~" in line and "on your pattern" in line
    assert "heading" not in line


def test_budget_falls_back_to_linear_pace_and_says_so(utc_now):
    """No learned profile is not no answer — but the reader has to know which
    model spoke, because the two disagree and only one knows your Tuesday."""
    seven = seven_entry(50, 2 * DAY, utc_now)
    seven["pace"] = 0.7
    line = budget_of(advice(utc_now, [seven], projection=None))
    assert "lands ~70% at this pace" in line


def test_no_landing_ever_exceeds_the_pool(utc_now):
    """`lands ~177%` is not a landing. Linear pace has the same ceiling as
    the walk, or the fallback reintroduces the bug the walk just lost."""
    seven = seven_entry(90, 2 * DAY, utc_now)
    seven["pace"] = 2.4
    line = budget_of(advice(utc_now, [seven], projection=None))
    assert "lands ~100% at this pace" in line


def test_one_wall_per_window(utc_now):
    """The walk projects dry AND linear pace already warned: the block gets
    one wall, not two dates for one window."""
    seven = seven_entry(80, 2 * DAY, utc_now)
    seven["pace"] = 1.6
    seven["cap_eta"] = utc_now + timedelta(hours=6)
    projection = cc.project_week(flat_profile(40), seven, utc_now)
    assert projection is not None and projection[1] is not None
    lines = advice(utc_now, [seven], projection=projection)
    assert len(warns_of(lines)) == 1


def test_a_dry_walk_is_a_date_not_a_percentage(utc_now):
    """Above 100 the useful fact is WHEN, and the budget line agrees with it
    by landing on exactly 100 — one model, read twice."""
    seven = seven_entry(44, 2 * DAY, utc_now)
    projection = cc.project_week(flat_profile(60), seven, utc_now)
    lines = advice(utc_now, [seven], projection=projection)
    (warn,) = warns_of(lines)
    assert warn.startswith("7d dry ~")
    assert "before reset" in warn and "hard stop" in warn
    assert "lands ~100%" in budget_of(lines)


def test_extra_usage_changes_the_dry_tail(utc_now):
    seven = seven_entry(44, 2 * DAY, utc_now)
    projection = cc.project_week(flat_profile(60), seven, utc_now)
    lines = advice(
        utc_now, [seven], projection=projection,
        data={"extra_usage": {"is_enabled": True}},
    )
    assert "extra usage billing" in warns_of(lines)[0]


# --- the runway ----------------------------------------------------------

def test_budget_counts_windows_ahead_not_the_one_you_are_in(utc_now):
    seven = seven_entry(50, 10 * 3600, utc_now)
    five = five_entry(10, 3600, utc_now)
    assert "~2 windows left" in budget_of(advice(utc_now, [five, seven]))


def test_last_window_when_the_week_ends_inside_this_one(utc_now):
    seven = seven_entry(75, 3 * 3600, utc_now)
    five = five_entry(20, 3.5 * 3600, utc_now)
    line = budget_of(advice(utc_now, [five, seven]))
    assert line.startswith("budget: last window · 25% left")
    assert "/window" not in line


def test_one_window_ahead_keeps_the_count_and_the_grammar(utc_now):
    """Calling two windows the last one to save a redundant clause states
    something false about the week."""
    seven = seven_entry(75, 8 * 3600, utc_now)
    five = five_entry(20, 3.5 * 3600, utc_now)
    line = budget_of(advice(utc_now, [five, seven]))
    assert "~1 window left" in line
    assert "25.0%/window stays even" in line


def test_the_ration_and_the_prediction_are_different_clauses(utc_now):
    """`N%/window stays even` is what to SPEND; `lands ~N%` is where you END
    UP. They answer different questions and the line may not blur them."""
    seven = seven_entry(44, 2 * DAY, utc_now)
    five = five_entry(10, 3600, utc_now)
    projection = cc.project_week(flat_profile(10), seven, utc_now)
    line = budget_of(advice(utc_now, [five, seven], projection=projection))
    assert "%/window stays even" in line and "lands ~" in line


# --- the hours you keep --------------------------------------------------

def rested(rate: float = 10, *, days: int = 30, rest: Iterable[int] = range(8)) -> dict:
    """A learned profile that knows this account sleeps 00:00-08:00."""
    forecast = flat_profile(rate, days=days)
    forecast["hour_profile"] = hour_shape(rest)
    return forecast


def test_the_ration_divides_by_the_windows_you_are_awake_for(utc_tz, utc_now):
    """Nine windows of clock, six of them waking. Dividing 56 points across
    all nine rations the reader across windows they sleep through, and the
    honest number to spend per window they will actually see is higher."""
    seven = seven_entry(44, 48 * 3600, utc_now)
    five = five_entry(10, 3 * 3600, utc_now)
    line = budget_of(advice(utc_now, [five, seven], forecast=rested()))
    assert "~9 windows left · ~6 awake · 9.3%/window stays even" in line


def test_the_awake_clause_waits_for_the_same_evidence_the_walk_does(utc_tz, utc_now):
    """Under two weeks of history there is no shape to speak of, and the
    ration goes back to counting clock windows."""
    seven = seven_entry(44, 48 * 3600, utc_now)
    five = five_entry(10, 3 * 3600, utc_now)
    line = budget_of(advice(utc_now, [five, seven], forecast=rested(days=10)))
    assert "awake" not in line
    assert "6.2%/window stays even" in line


def test_an_account_that_burns_around_the_clock_gets_no_extra_clause(utc_tz, utc_now):
    """Nothing to refine: every window ahead is one you are up for, and a
    clause that restates the count beside it is noise."""
    seven = seven_entry(44, 48 * 3600, utc_now)
    five = five_entry(10, 3 * 3600, utc_now)
    forecast = flat_profile(10)
    forecast["hour_profile"] = {str(h): 1.0 for h in range(24)}
    line = budget_of(advice(utc_now, [five, seven], forecast=forecast))
    assert "awake" not in line
    assert "6.2%/window stays even" in line


def test_a_corrupt_shape_leaves_the_budget_exactly_as_it_was(utc_tz, utc_now):
    seven = seven_entry(44, 48 * 3600, utc_now)
    five = five_entry(10, 3 * 3600, utc_now)
    forecast = flat_profile(10)
    forecast["hour_profile"] = {"0": 1.0}
    assert budget_of(advice(utc_now, [five, seven], forecast=forecast)) == budget_of(
        advice(utc_now, [five, seven])
    )


def test_nothing_awake_ahead_states_the_count_and_stops(utc_tz, utc_now):
    """Two windows left and you sleep through both. `~0 awake` is not a
    ration and `28.0%/window` is not a rate anyone can spend, so the line
    says what is left of the week and nothing it cannot stand behind."""
    night = utc_now.replace(hour=22)
    seven = seven_entry(44, 9 * 3600, night)
    five = five_entry(10, 2 * 3600, night)
    line = budget_of(advice(night, [five, seven], forecast=rested()))
    assert line == "budget: ~2 windows left"


def test_the_awake_count_stops_where_access_does(utc_tz, utc_now):
    """One horizon per block. With the budget truncated to a day of access
    left, the awake count describes that day — not the five days of quota
    behind it, which would put `~5 awake` beside `~5 windows left`."""
    seven = seven_entry(30, 5 * DAY, utc_now)
    five = five_entry(10, 3600, utc_now)
    line = budget_of(advice(
        utc_now, [five, seven],
        access=(utc_now + timedelta(days=1), "trial ends ~Aug 27"),
        forecast=rested(),
    ))
    assert "~5 windows left · ~3 awake · 23.3%/window stays even" in line
    assert "trial ends ~Aug 27" in line


# --- two pools, one wall -------------------------------------------------

def two_pools(
    now, seven: int, scope: int, *, name: str = "fable",
    remaining: float = 2 * DAY, skew: float = 0.0, active: bool = False,
) -> list[dict]:
    """An account pool and one model-scoped pool ending the same week."""
    return [
        seven_entry(seven, remaining, now),
        scoped_entry(name, scope, remaining + skew, now, active=active),
    ]


def test_the_mix_says_what_a_7d_point_buys(utc_now):
    """81% of the account gone against 63% of the model's own pool: this week
    bought 0.78 scoped points per 7d point, so the 19 points left on the
    account reach 15 of the model's 37 and the other 22 expire. Same split,
    read two ways — the row names the reachable half and the pool it is a
    half of, because 22 wasted points is not something anyone can act on."""
    line = mix_of(advice(utc_now, two_pools(utc_now, 81, 63)))
    assert line == (
        "fable: ~15% of its 37% left reachable at this mix · "
        "heavier fable extracts more"
    )


def test_the_strand_sits_directly_above_the_budget_line(utc_now):
    """It qualifies the very headroom the budget rations; a reader who meets
    the ration first has already spent the number being qualified."""
    lines = advice(utc_now, two_pools(utc_now, 81, 63))
    assert [level for level, _ in lines[-2:]] == ["info", "info"]
    assert lines[-2][1] == mix_of(lines)
    assert lines[-1][1] == budget_of(lines)


def test_no_scoped_pool_no_reading(utc_now):
    assert mix_of(advice(utc_now, [seven_entry(81, 2 * DAY, utc_now)])) is None


def test_only_a_weekly_pool_can_be_read_against_the_week(utc_now):
    """A scoped 5h cap shares no wall with the 7d: its utilization is a
    fraction of a different, shorter window and the ratio would be fiction."""
    hourly = five_entry(63, 3 * 3600, utc_now)
    hourly["name"] = "fable"
    lines = advice(utc_now, [seven_entry(81, 2 * DAY, utc_now), hourly])
    assert mix_of(lines) is None


def test_a_young_week_has_no_mix_yet(utc_now):
    """Under a day in, both counters are a handful of samples off zero and
    the ratio between them swings with every one of them."""
    assert mix_of(advice(utc_now, two_pools(utc_now, 81, 63, remaining=6.5 * DAY))) is None


def test_two_walls_are_two_weeks(utc_now):
    """A couple of minutes is clock skew. Five is a scoped pool counting a
    different week, and two counters that did not start together are not a
    ratio — the gate is what keeps this honest if the walls ever split."""
    assert mix_of(advice(utc_now, two_pools(utc_now, 81, 63, skew=90))) is not None
    assert mix_of(advice(utc_now, two_pools(utc_now, 81, 63, skew=300))) is None
    assert mix_of(advice(utc_now, two_pools(utc_now, 81, 63, skew=-300))) is None


def test_an_untouched_model_is_not_a_mix(utc_now):
    """4% on the scoped pool is the underuse question, not a rate: read as a
    mix it promises a strand nobody was ever heading for."""
    assert mix_of(advice(utc_now, two_pools(utc_now, 81, 4))) is None
    assert mix_of(advice(utc_now, two_pools(utc_now, 81, 5))) is not None


def test_which_cap_binds_is_a_question_only_near_the_end(utc_now):
    """At 59% of the account nothing binds yet; the pools have four more days
    to change their minds about each other."""
    assert mix_of(advice(utc_now, two_pools(utc_now, 59, 40))) is None
    assert mix_of(advice(utc_now, two_pools(utc_now, 60, 40))) is not None


def test_two_pools_draining_together_have_nothing_to_say(utc_now):
    """Nine points of strand is rounding wearing advice; ten is a model you
    could actually be running harder."""
    assert mix_of(advice(utc_now, two_pools(utc_now, 80, 73))) is None
    assert mix_of(advice(utc_now, two_pools(utc_now, 80, 72))) is not None


def test_a_capped_pool_is_its_own_notice(utc_now):
    """Once either wall is reached the block already says so, and a mix rate
    read off a counter that stopped moving is a reading of the past."""
    assert mix_of(advice(utc_now, two_pools(utc_now, 100, 63))) is None
    assert mix_of(advice(utc_now, two_pools(utc_now, 81, 100))) is None


def test_the_running_model_answers_before_the_deeper_one(utc_now):
    """Depth is a guess at which pool the reader cares about; is_active is
    the account saying it outright."""
    windows = [
        seven_entry(81, 2 * DAY, utc_now),
        scoped_entry("opus", 70, 2 * DAY, utc_now),
        scoped_entry("fable", 63, 2 * DAY, utc_now, active=True),
    ]
    assert mix_of(advice(utc_now, windows)).startswith("fable: ~15% of its 37%")


def test_with_no_model_running_the_deepest_pool_answers(utc_now):
    windows = [
        seven_entry(81, 2 * DAY, utc_now),
        scoped_entry("opus", 70, 2 * DAY, utc_now),
        scoped_entry("fable", 63, 2 * DAY, utc_now),
    ]
    assert mix_of(advice(utc_now, windows)).startswith("opus: ~16% of its 30%")


# --- boundaries ----------------------------------------------------------

def test_access_end_truncates_the_budget_and_mutes_the_landing(utc_now):
    """Windows you cannot spend are not budget, and a landing at a reset you
    will not see is not a fact about you."""
    seven = seven_entry(30, 5 * DAY, utc_now)
    five = five_entry(10, 3600, utc_now)
    lines = advice(
        utc_now, [five, seven],
        access=(utc_now + timedelta(days=1), "trial ends ~Aug 27"),
    )
    line = budget_of(lines)
    assert "trial ends ~Aug 27" in line
    assert "lands ~" not in line


def test_a_capped_week_says_when_it_comes_back(utc_now):
    seven = seven_entry(100, 2 * DAY, utc_now)
    (warn,) = warns_of(advice(utc_now, [seven]))
    assert warn.startswith("7d capped - resets in")


def test_hot_segments_stop_the_eye_only_near_the_cap(utc_now):
    assert cc.advice_segment_hot("lands ~95% on your pattern")
    assert not cc.advice_segment_hot("lands ~52% on your pattern")
    assert cc.advice_segment_hot("trial ends ~Aug 27")
    assert not cc.advice_segment_hot("6.2%/window stays even")


# --- the strip -----------------------------------------------------------

def test_burn_glyphs_are_one_unicode_block(utc_now):
    """The baseline is the shortest BAR, not a modifier letter borrowed from
    the text font — a ledger whose zero line renders at a different height
    and width than its bars is not a ledger."""
    ladder = [cc.burn_glyph(c) for c in (0, 0.4, 1, 2, 3, 5, 9, 13, 18, 40)]
    assert ladder == list("▁▁▂▂▃▄▅▆▇█")
    assert all("▀" <= g <= "▟" for g in ladder)
    assert "ˍ" not in ladder


def test_the_budget_count_comes_from_clocks_not_from_the_drawing(utc_now):
    """The sentence owns the number, and the number is windows_ahead."""
    seven = seven_entry(40, 30 * 3600, utc_now)
    five = five_entry(10, 2 * 3600, utc_now)
    want = cc.windows_ahead(seven, five)
    assert f"~{want} windows left" in budget_of(advice(utc_now, [five, seven]))


def test_the_ledger_never_drifts_more_than_a_cell_from_the_count(utc_now):
    """The strip is a grid on the period start; your 5h windows are anchored
    to the 5h reset, and 34 cells span 170h against a 168h period. So the
    hollow run right of ▮ sits within ONE cell of the budget's count — never
    a whole window out, which is what an off-by-one in either definition
    would look like."""
    worst = 0
    for mins_left in range(5, 168 * 60, 53):
        for five_left in range(5, 300, 37):
            seven = seven_entry(40, mins_left * 60, utc_now)
            five = five_entry(10, five_left * 60, utc_now)
            strip = cc.format_window_ledger(seven, {}, None, utc_now, color=False)
            if "▮" not in strip:
                continue
            drawn = strip.split("▮", 1)[1].count("▯")
            worst = max(worst, abs(drawn - cc.windows_ahead(seven, five)))
    assert worst == 1


# --- the night on the ledger ---------------------------------------------

ANSI_OR_CHAR = re.compile(r"\x1b\[[0-9;]*m|.")


@pytest.fixture
def tty(monkeypatch):
    """The ledger tints only on a terminal, and pytest's stdout is not one."""
    monkeypatch.setattr(cc, "supports_color", lambda: True)


def tinted(strip: str) -> list[tuple[str, str]]:
    """(glyph, tint in force) per cell — the row as the eye actually gets it."""
    tint, cells = "", []
    for token in ANSI_OR_CHAR.findall(strip):
        if token.startswith("\x1b"):
            tint = "" if token == cc.RESET else tint + token
        else:
            cells.append((token, tint))
    return cells


def ahead(strip: str) -> list[tuple[str, str]]:
    """Only the cells right of ▮ — the future this row is now shaping."""
    cells = tinted(strip)
    return cells[next(i for i, (g, _) in enumerate(cells) if g == "▮") + 1:]


def week_grid(now, **kw) -> dict:
    """A 7d window whose 5h grid lands on round hours: a reset two days out
    opens slot 0 at 09:00 and puts slot 24 exactly on `now`, so the nine
    slots ahead are 14:00, 19:00, 00:00, 05:00, 10:00, 15:00, 20:00, 01:00
    and 06:00 — stated, not counted off a drawing."""
    return {**seven_entry(44, 2 * DAY, now), **kw}


def at(day: int, hour: int, minute: int = 0) -> datetime:
    return datetime(2026, 8, day, hour, minute, tzinfo=timezone.utc)


def night_hours() -> list[float]:
    return cc.hour_multipliers({"hour_profile": hour_shape(range(8))})


def test_a_slot_is_a_night_when_under_half_of_it_is_waking(utc_tz):
    """The slot's own wall span decides, never the hour it opens in — every
    5h slot on a 24h clock straddles something, and the two that straddle
    the same night's edges are not the same kind of slot."""
    mult = night_hours()
    assert cc.slot_is_rest(mult, at(26, 3), at(26, 8))  # dead centre of it
    assert not cc.slot_is_rest(mult, at(26, 10), at(26, 15))  # dead centre of the day
    assert cc.slot_is_rest(mult, at(26, 23), at(27, 4))  # opens awake, sleeps four
    assert not cc.slot_is_rest(mult, at(26, 7), at(26, 12))  # opens asleep, wakes four


def test_half_a_window_awake_is_still_a_window(utc_tz):
    """REST_SLOT_AWAKE_MIN_SECS is a floor to fall BELOW: exactly 2h30m of
    waking time is a window you can spend, a minute less is a night."""
    mult = night_hours()
    span = (at(26, 5, 30), at(26, 10, 30))
    assert cc.awake_seconds(mult, *span) == cc.REST_SLOT_AWAKE_MIN_SECS == 9000
    assert not cc.slot_is_rest(mult, *span)
    assert cc.slot_is_rest(mult, at(26, 5, 29), at(26, 10, 29))


def test_the_future_stops_being_one_number_and_becomes_a_shape(utc_tz, utc_now, tty):
    """Nine slots ahead, three of them nights, all of them still ▯ — the
    glyph is the fact and the tint is the refinement. 05:00-10:00 is a night
    (two waking hours) and 20:00-01:00 is not (four): the same edge, two
    different slots."""
    strip = cc.format_window_ledger(
        week_grid(utc_now), {}, None, utc_now, forecast=rested()
    )
    assert [g for g, _ in ahead(strip)] == list("▯" * 9)
    assert [t == cc.DIM for _, t in ahead(strip)] == [
        False, False, True, True, False, False, False, True, False
    ]
    # ▮ and every cell of the record left of it are untouched
    bare = cc.format_window_ledger(week_grid(utc_now), {}, None, utc_now)
    assert strip.split("▮")[0] == bare.split("▮")[0]
    assert dict(tinted(strip))["▮"] == cc.BOLD


def test_an_unlearned_row_is_the_row_it_always_was(utc_tz, utc_now, tty):
    """Byte-identical, tints and all. A tool that has not learned your hours
    does not get to guess at them, and every way of not knowing them — no
    field, a truncated one, a nonsense one, a real one with a fortnight of
    history missing behind it — lands on the same row."""
    for color in (True, False):
        was = cc.format_window_ledger(week_grid(utc_now), {}, None, utc_now, color=color)
        for forecast in (
            None,
            flat_profile(10),
            rested(days=10),
            {**flat_profile(10), "hour_profile": {"0": 1.0}},
            {**flat_profile(10), "hour_profile": {str(h): 30.0 for h in range(24)}},
        ):
            assert cc.format_window_ledger(
                week_grid(utc_now), {}, None, utc_now, color=color, forecast=forecast
            ) == was


def test_a_dry_guess_may_not_delete_a_window(utc_tz, utc_now, tty):
    """A future cell is a slot, never a verdict. The dry projection used to
    overwrite ahead-cells as red × and was read, live, as deleted windows
    (statusline v0.39.0, same day); the wall's owner is the `7d dry` advice
    row under this ledger. With and without a cap_eta, the drawn cells are
    identical — hollow to the edge, dim where the night falls."""
    dry = week_grid(utc_now, cap_eta=utc_now + timedelta(hours=15))
    calm = week_grid(utc_now)
    with_dry = cc.format_window_ledger(dry, {}, None, utc_now, forecast=rested())
    without = cc.format_window_ledger(calm, {}, None, utc_now, forecast=rested())
    assert with_dry == without
    assert "×" not in with_dry
    cells = ahead(with_dry)
    assert all(g == "▯" for g, _ in cells)
    assert any(tint == cc.DIM for _, tint in cells)
