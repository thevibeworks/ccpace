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
           ▅▁▂ ▃▅ˍ▃▅ ▃▃▁▂▁ ▅ˍ▂▁▁ ˍˍˍ▃▅ ˍˍˍ▅ ▆▆ˍ▂▮▯▯
           budget: ~3 windows left · 20%/window stays even · heading ~52%
           forecast: +19% rest of week on your pattern · lands ~58%
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
▁▂▃▄▅▆▇█   burned; height = 7d points that 5h window cost (▁ ≤2 … █ >20)
ˍ          ran, negligible — a bar of height zero, on the baseline
░          unknown — no sample; never drawn as idle
▮          now
▯          ahead — the hollow of ▮
×          pace won't cover it
┤          access ends here (trial / period end)
```

34 cells, oldest left, a gap at each local midnight *in history only*.
Tint = the 7d row's color; nothing else in the row is colored.

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

Lowercase, terse: `heading`, `lands`, `stays even`, `windows left`.
Numbers first. One line per thought; `·` between clauses.
