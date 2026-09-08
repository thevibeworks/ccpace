---
name: ccpace
description: Check Claude and Codex subscription usage, quota pace, and account budgets. Use when the user asks how much quota is left, whether they are burning too fast, or when limits reset.
---

# ccpace

Run the monitor and interpret its output for the user.

```
"${CLAUDE_PLUGIN_ROOT}/bin/ccpace" --once   # one snapshot, all providers
"${CLAUDE_PLUGIN_ROOT}/bin/ccpace" --json   # structured account snapshots
"${CLAUDE_PLUGIN_ROOT}/bin/ccpace" --raw    # legacy Claude-only payload
```

The one-shot view identifies provider and account, each limit's used
percentage and reset, and any source error. `--json` has `schema: 1` and
an `accounts` array with limits, freshness, reset inventory, and balance.
Keep provider/account identities separate. Banked resets are not credits;
ccpace does not redeem them. A missing reset is not unlimited capacity.

Reading the legacy Claude compact output (`--provider claude` in a pipe):

- Each account block: header rule with tier + alias + subscription period
  end; one row per rate-limit window (5h, 7d, per-model).
- Bars: `█` both usage and time passed, `▓` usage ahead of time (hot),
  `▒` time ahead of usage (headroom), `░` untouched.
- The ledger strip under the 7d row: one cell per 5h slot of the period;
  `▂`..`█` what a slot burned, `▁` the baseline (ran, cost nothing),
  `░` unknown, `▮` = now, `▯` = a slot ahead (dim: one the user likely
  sleeps through), `┤` = access ends there. A dry projection never
  overwrites a cell — the `7d dry` advice row states the wall. It is a
  grid on the period start,
  so read it for SHAPE — the budget line's count comes from the clocks
  and is the number to quote.
- `!` lines are walls: a pace or learned-forecast projection of when the
  pool runs out, with the date and the gap before reset.
- The budget line is the week in one breath: windows left, the RATION
  (`N%/window stays even` — spend that and you land exactly on 100), and
  the PREDICTION (`lands ~N%`, tagged `on your pattern` when it comes
  from the learned weekday profile and `at this pace` when it is linear).
  Two different futures; do not blur them when you report.
- `~N awake` between the two is the runway with the hours this account
  rests taken out, and it is the RATION's denominator when present. Quote
  both counts. No ration at all means nothing ahead is awake.
- A row directly above the budget (`fable: ~15% of its 37% left reachable
  at this mix`) means the account's 7d cap will end the week before that
  model's own pool empties. The number is what the CURRENT mix reaches;
  the rest of the pool expires unless that model runs heavier.

Answer with the numbers, not the raw dump: utilization, time to reset,
windows left, where it lands and by which model, and whether a wall
fired. Never run bare `ccpace`, `--watch`, or `--calendar` from an agent tool call; all
need a real interactive terminal. For users who want the calendar, provide
`uvx ccpace --calendar`; `--calendar --demo` previews it without account
access. Spectrum, Quiet, and Paper are selected with `--theme`.

Calendar custom-notifier events carry observation and forecast provenance,
condition IDs, and transition IDs. They report provider limits; they are
not instructions to pause, switch models, or spend remaining credits.
