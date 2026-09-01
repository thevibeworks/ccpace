# DESIGN — ccpace's language

Same family as [claude-code-statusline/DESIGN.md](https://github.com/thevibeworks/claude-code-statusline/blob/main/DESIGN.md):
same glyphs, same math, same log. ccpace is the full-screen view of what
the statusline shows in one line.

## The block

```
── [20x] feast · period ends ~Sep 10 ─────────────────────────────
5h     13% █▒▒▒▒▒▒▒░░  4h 11m   @04:00         +3%  0.6x
7d     42% ████▒▒▒▒░░  10h 29m  @Wed 19 09:00       0.7x
fable  70% ███████▒▒░  10h 29m  @Wed 19 09:00       0.7x
           ▅▂▃ ▄▅▁▄▅ ▄▄▂▃▂ ▅▁▃▂▂ ▁▁▁▄▅ ▁▁▁▅ ▆▆▁▃▮▯▯
           budget: ~2 windows left · 29%/window stays even · lands ~58% on your pattern
```

Three bands, one question each: **rows** how much of this window is
left; **ledger** where the week went; **advice** what to do about it.
Every account is one block; the rule is the splitter and carries
identity (tier, alias, provenance).

## Rows

One grammar per row: `name  pct  bar  remaining  @reset  Δ  pace`.
The bar merges usage with elapsed time — `█` both passed, `▓` usage ahead
(hot), `▒` time ahead (headroom), `░` untouched. Pace = used ÷ elapsed
(`>1x` = capping early). Reset is wall clock (`@04:00`, `@Wed 19 09:00`),
never a countdown that rots.

## Ledger

```
▂▃▄▅▆▇█    burned; height = 7d points that 5h slot cost (▂ ≤2 … █ >20)
▁          baseline: ran, negligible — the shortest bar of the same block,
           so the zero line and the bars share one font and one width
░          unknown — no sample; never drawn as idle
▮          now
▯          ahead — the hollow of ▮
▯ dim      ahead, but you sleep through most of it
×          pace won't cover it
┤          access ends here (trial / period end)
```

34 cells, oldest left, a gap at each local midnight *in history only*.
Tint = the 7d row's color; nothing else in the row is colored.

A GRID anchored to the period start, not a row of your real 5h windows —
those follow the 5h reset, in phase only by coincidence, and 34 cells span
170h against a 168h period. That grid is what makes a day's windows land
under one day. Read the row for shape; the budget line owns the count,
which comes from clocks. They sit within one cell of each other — and so
do the dim cells and `~N awake`, for exactly the same reason.

The night is the row's one refinement carried by tint alone, and the one
place that is honest: a dim ▯ is still a window ahead, so the tint says
only how likely that capacity is to be reachable and a reader who cannot
see it loses nothing they could have acted on. It makes the future a SHAPE
instead of a count. A slot is a night when under half of it (9000 s) falls
in hours you are awake for — the slot's whole wall span, not the hour it
opens in, because 20:00–01:00 and 05:00–10:00 straddle the same night and
only one of them is a window you can spend. Gated on the same evidence the
walk needs; unlearned, the row is byte-for-byte what it was. `×` wins over
rest — unreachable beats unlikely — and ▮ and the record are untouched.

## Advice

```
 !  7d dry ~Wed 14:20, 19h before reset; then hard stop until reset
    fable: ~15% of its 37% left reachable at this mix · heavier fable extracts more
    budget: ~9 windows left · ~6 awake · 9.3%/window stays even · lands ~91% on your pattern
             runway            REST       RATION                   PREDICTION
```

`!` is a wall: a date, and the gap before the reset. The budget line is
the week in one breath, and its two futures are different kinds of
statement — the RATION is what to spend (that rate lands exactly on 100),
the PREDICTION is where your own behaviour takes you. Tagged `on your
pattern` when the learned weekday walk spoke, `at this pace` when it was
linear, so the reader always knows which model answered.

`~6 awake` is the runway with the nights taken out. Claude Code can work
around the clock; you cannot, and a ration that divides the pool across
windows you sleep through asks you to hit a number lower than the one you
can actually spend. The clause appears only when the hour shape is learned
and the two counts differ, and it names the RATION's denominator by sitting
beside it — nine windows, six of them yours, 9.3% each. Nothing awake ahead
is not a rate: the line states the count and stops.

The scoped row is the other pool, and it is an opportunity rather than a
wall. The 7d cap ends the week for every model, so a model-scoped cap
draining slower than the account's simply never empties. Both counters
start at the same reset, which makes their ratio this week's MIX RATE with
no history behind it: at 81/63 the week bought 0.78 fable points per 7d
point, so the 19 points left on the account reach 15 of fable's 37. Stated
as the reachable half, never the wasted one — 22 expiring points is not
something anyone can act on, and running fable heavier is. It sits above
the budget because it qualifies the same headroom the budget then rations,
and it appears only when the reading is honest: one wall (both resets
within two minutes), the account deep enough for the question to exist,
the model actually touched this week, neither pool capped, and at least ten
points on the table.

One model per block. A landing is capped at 100 — above that the fact is
the DATE, and the `!` row is where it goes. Two lines describing one week
with two numbers is not more information; it is an argument the reader has
to settle.

## Provenance on the rule

`(cached)` frozen at 100% until reset · `(stale 12m · !429)` last fetch
failed, numbers this old · `(idle 12m)` Claude Code did nothing since,
so nothing was asked. Never a blank block: the last cache beats an empty
frame.

## Requests

Ask only when the answer can have changed.

- Pool first: a `usage.cache` younger than 60 s — anyone's — is the fetch.
- Idle gate (watch): fetched before, no reset passed, no Claude Code
  activity since (`history.jsonl`, statusline session state — across every
  container sharing `~/.claude`) → no request; `r` overrides.
- Reset boundaries wake the loop; the poll interval (15 min ± 10%) is the
  ceiling, not the metronome.
- Failure: last cache + badge, retry next tick; never a lockout, never
  `usage.err` (that file is the statusline's).
- Profile 24 h (tier comes from the credentials file); prepaid 1 h and
  only when credits were ever enabled.

## Words

Lowercase, terse: `lands`, `stays even`, `windows left`, `awake`.
Numbers first. One line per thought; `·` between clauses.
