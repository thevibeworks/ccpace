"""The sentences. One model per block, and every number named for what it is.

What this file exists to prevent: a budget line saying `heading ~61%` off
linear pace with a forecast line under it saying `lands ~177%` off a
different model — same week, same breath, three-fold apart, and no way for
the reader to tell which one was the forecast.
"""

from __future__ import annotations

from datetime import timedelta

from conftest import cc, five_entry, flat_profile, seven_entry

DAY = 86400


def advice(now, windows, **kw) -> list[tuple[str, str]]:
    return cc.build_advice(kw.pop("data", {}), windows, now=now, **kw)


def budget_of(lines: list[tuple[str, str]]) -> str:
    return next(msg for level, msg in lines if msg.startswith("budget:"))


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
