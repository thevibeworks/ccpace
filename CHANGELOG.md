# Changelog

## v0.7.0 — a guess may not delete a window (2026-09-01)

**The `×` cell is retired.** The ledger used to overwrite dry-projected
ahead-cells as red `×`, and drawn out in a run they read as *deleted
windows* — measured live the day statusline's unfold exposed the same
run on its row (`▮▯▯▯×××`: six windows to the reset, counted as three
by the person the row exists for). A future cell is a slot, never a
verdict. The wall already has an owner with better gates and an exact
time: the `7d dry ~...` advice row directly under this ledger. Every
cell ahead now draws hollow — dim where your learned hours say you
sleep — to the grid's edge.

Ships with statusline v0.39.0 ("a guess may not delete a window"),
which retires its future `×` cells the same way and keeps the mark only
in its folded token, where no per-cell shape exists to say it. No
`forecast.cache` change of any kind.

## v0.6.0 — the night on the ledger (2026-09-01)

v0.4.0 taught the forecast that you sleep. The ledger still did not know:

```
▂▃▅▁▂▄█▃▁▁▂▅▄▃▁▂▃▅▄▂▁▃▄▅▃▂▮▯▯▯▯▯▯▯
```

Nine hollow cells, all drawn the same, and three of them are the middle of
two nights. The row said "nine slots ahead" while the budget line beside it
said `~6 awake` — one surface counting clock, the other already counting
yours.

### The future is a shape, not a count

An ahead-cell whose 5h slot has under `REST_SLOT_AWAKE_MIN_SECS` (9000 —
half a window) of waking seconds now draws DIM. Same ▯: the glyph is the
fact, the tint is the refinement, and a reader who cannot see the tint
loses nothing they could have acted on — a dim ▯ is still a window ahead,
it is just one the capacity is unlikely to reach. Waking hours are the
v0.4.0 arithmetic exactly (`mult >= REST_MULT_MAX` over the slot's real
wall span), so nothing here is a second opinion about your day.

The wall span is the whole rule. 20:00–01:00 and 05:00–10:00 straddle the
same night's two edges and the hour a slot OPENS in gets both of them
wrong: the first is four waking hours and a window you can spend, the
second is two and a night. On the 5h grid every slot straddles something.

Gated on the evidence the walk already needs — a valid `hour_profile` and
`FORECAST_MIN_DAYS` of history. Unlearned, the row is byte-for-byte the
row it was in v0.5.0, tints and all, and the tests hold it there against
every way of not knowing: no field, a truncated one, a nonsense one, a
real one with a fortnight of history missing behind it.

`×` beats rest — a slot the pool will not cover is unreachable for a
stronger reason than sleep, and drawing it as a night would hide that. ▮
and every cell of the record are untouched.

### Same rule on both surfaces

`REST_SLOT_AWAKE_MIN_SECS` is a shared READING RULE, not a cache field:
statusline v0.38.0 dims the same slots on its own 7d strip (and each rest
hour on the 5h one) off the same `forecast.cache`. `schema` stays 2; no
field moved. Documented in `docs/statusline-interop.md`. The dim cells and
the budget's `~N awake` may differ by one — the strip is a grid on the
period start, the count comes from real clocks — the same tolerance the
window count itself already carries.

Internally the two now share one `awake_seconds()`; the budget's count and
the ledger's nights were never allowed to be two implementations.

## v0.5.0 — two pools, one wall (2026-09-01)

An account at 81% of its week with a model-scoped pool at 63% has two
counters heading for the same wall at different speeds, and nothing on the
block said which cap binds or what it costs:

```
7d     81% ████████▓░  2d 3h   @Wed 19 09:00       1.2x
fable  63% ██████▒▒▒░  2d 3h   @Wed 19 09:00       0.9x
```

The 7d cap ends the week for every model, so those 37 fable points are not
37 points of headroom — at this week's mix, 22 of them expire untouched.

### The ratio is the estimator

Both counters start at the same reset instant, which makes the live ratio
between them this week's MIX RATE — scoped points per 7d point — with no
history behind it at all:

```
mix       = scope / seven
reachable = round((100 - seven) * scope / seven)
strand    = round(100 * (seven - scope) / seven)   == (100 - scope) - reachable
```

81/63 gives mix 0.78, reachable 15, strand 22. Mining the corpus for the
same week's dF/dS put it at 0.77, so this is not an approximation of the
measurement, it is the measurement — available in the payload already on
screen. No new `forecast.cache` field, no schema question, nothing to
learn and nothing to wait two weeks for.

What is NOT published: the pure-scope coupling, what a scoped point costs
the account when only that model runs. n=22, and the band is wide enough
to be fluent and wrong.

### One row, above the budget

```
fable: ~15% of its 37% left reachable at this mix · heavier fable extracts more
budget: ~9 windows left · ~6 awake · 9.3%/window stays even · lands ~91% on your pattern
```

An info row immediately before the budget line, because it qualifies the
very headroom the budget then rations. It states the reachable half rather
than the strand: 22 wasted points is not something a reader can act on,
and running that model heavier is. Where the payload names no running
model the deepest scoped pool answers, unless one declares itself active —
depth is a guess at which pool the reader cares about, `is_active` is the
account saying it outright.

Gated so the ratio stays honest, and statusline v0.37.0 gates its own
notice on the same five: one wall (both resets within 120 s — Anthropic
could split them someday), `SCOPE_MIX_MIN_7D = 60` (which cap binds is a
question only near the end), `SCOPE_MIX_MIN_SCOPE = 5` (an untouched model
is the underuse question, not a mix), neither pool capped (that is its own
notice), `SCOPE_STRAND_MIN_PCT = 10` (under that it is rounding wearing
advice), and the existing young-week guard. The constants are shared
READING RULES, documented in `docs/statusline-interop.md`; statusline adds
two mutes ccpace has no mechanism for and did not invent.

## v0.4.0 — the hours you keep (2026-09-01)

Claude Code can work 24/7. You cannot, and the forecast did not know the
difference: it learned a WEEKDAY profile and then burned it flat through
the night. So a week that really ran out on Thursday morning printed

```
7d dry ~Thu 03:00, 30h before reset; then hard stop until reset
budget: ~9 windows left · 6.2%/window stays even
```

A wall placed mid-sleep is a false alarm at 11pm and a missed warning at
09:00, and a ration divided across windows you sleep through asks you to
hit a number lower than the one you can actually spend. Both are the same
missing fact. The corpus already held it: burn credited by the envelope
pass carries a timestamp, and hours that never burn across weeks are the
hours you rest.

### `hour_profile`: the shape of your day, in the shared cache

24 multipliers by local hour, mean 1.0, so the rate at hour h is
`weekday_rate * mult[h]` and a whole day still burns its weekday total —
only the shape inside the day changes. Built on the same pass and the same
constants as the weekdays: each envelope delta is credited to its local
`(day, hour)`, today is excluded (partial, never a training day), the rest
are EWMA-weighted at the 14-day half-life, and each hour's share of the
week becomes its multiplier.

Floored at 0.1 and renormalized to a mean of exactly 1, in that order, at
BUILD time. The floor is the hedge for the occasional overnight autonomous
run — a rest hour projects a tenth of a uniform hour, never zero — and the
order matters, since flooring after the normalization would publish a
shape whose mean is no longer 1. Build-time rounding matters because the
cache is SHARED: statusline computes the identical field off the same log,
and two writers rounding their own way is two answers to one week.
`schema` stays 2 — the model of the existing fields did not change, and a
reader that has never heard of the field keeps working. Contract in
`docs/statusline-interop.md` and `docs/data.md`.

Read defensively and never fatally: all 24 keys, every value numeric in
[0, 24], mean in [0.9, 1.1], or the walk takes flat and carries on. A bad
hour shape decides only whether the forecast knows when you sleep; the
weekday guards still decide whether it speaks at all.

### The walk steps by the hour

`project_week` now walks local hour boundaries instead of local days — at
most 169 segments for a week — and multiplies each segment's weekday rate
by that hour's shape. With no learned shape every multiplier is 1 and the
numbers are the day walk's to thirteen decimal places, which the suite
asserts. The dry warnings needed no copy change: the shaped walk moves the
dry TIME out of the night by itself, and that is the early-warning fix.

Two behaviours moved, both deliberately:

- The 24h blend (`max(weekday, recent_24h)` over the first day) is tested
  at the start of each segment, and a segment used to be a calendar day —
  so a blend that began 15h out ran to 39h. It now ends at 24h.
- A spring-forward day is 23 hours long and now burns 23 hours of quota.
  The day walk sized its segments by subtracting two datetimes that shared
  one tzinfo, which Python does on the WALL clock, so the skipped hour was
  credited anyway — twice a year, in every zone that moves. Segments are
  measured in absolute seconds off the local clock's own minute.

### `~6 awake`: the ration you can actually spend

```
budget: ~9 windows left · ~6 awake · 9.3%/window stays even · lands ~52% on your pattern
```

An hour whose multiplier is under `REST_MULT_MAX` (0.25, a shared
constant) is rest. Count the waking seconds between the end of the window
you are in and the end of the week, ceil them into 5h windows the same way
`windows_ahead` ceils — a partial window is still spendable — and clamp to
the window count itself. The clause appears only when the shape is learned
on at least two weeks of history and the two counts differ, and it names
the ration's denominator by sitting beside it. Nothing awake ahead is not
a rate: the line states the count and stops rather than divide by zero or
quote a number nobody can spend. When paid access ends before the reset,
the awake count is measured over the truncated span too — one horizon per
block, as the runway, the ledger's `┤` and the landing already were.

`windows_ahead` itself is untouched. The countdown invariant stays; the
budget line owns the refinement.

## v0.3.1 (2026-08-27)

Two readers, one rule. `load_account_history` partitioned by uuid and
dropped rows that carried none; `weekday_burn_forecast` let those same
rows through, on the theory that alias-scoped directories are
single-account. They are not — one real store held twelve uuids — and
two filters that disagree are a leak waiting for the first caller that
skips the loader. The forecast now applies the loader's rule and nothing
else.

The drop is counted. A row without a uuid is refused, never guessed
(thirteen of ninety-three in one store carry an email that would
identify them, which is exactly the temptation to resist on a log that
has already interleaved accounts), but a reader that discards
identifiable observations silently will discard a larger number just as
quietly. `load_account_corpus` returns the samples and a `Corpus`: files
read, rows kept, rows dropped for no uuid, rows of other accounts, and
the oldest kept timestamp.

That corpus is stamped into `forecast.cache`. `schema` versions the
MODEL and cannot say which samples it ran over: statusline reads one
directory, ccpace reads every store under the root, both count burn the
same way, both pass the gate — and the same account reads
`days_history: 28` or `301` depending on which binary rendered last.
`corpus: {uuid, files, samples, dropped_no_uuid, oldest}` makes that
visible in one `jq`. Informative, not a gate; `docs/data.md` has the
contract.

## v0.3.0 (2026-08-24)

The forecast was wrong, and it was wrong in the way that is hardest to
notice: it produced a fluent sentence. On a week with 56% of the pool
left, this tool printed

```
budget: ~10 windows left · 5.6%/window stays even · heading ~62% at reset Wed 26 09:00
forecast: +133% rest of week on your pattern · lands ~177% (251d history)
```

Two lines, one week, three numbers that cannot all be true, and no way
for the reader to tell which — if any — was the forecast. This release is
that block reduced to one model and one sentence.

### Burn is the rise of an envelope, not the sum of the deltas

Utilization inside a window only climbs, so a sample below the running
max is one of two things: a stale session reporting the numbers it last
saw, or a real reset. They need opposite answers — hold, or re-baseline —
and summing raw positive deltas gives neither. It credits the dip's
recovery as fresh burn, counting the same points twice.

The measured cost of that: 146 points of "burn" against a real 50-point
week, and 149%/day in a Thursday. It is the arithmetic behind `+133%`.

A stale window is now dropped on its key (a NEWER 7d `resets_at` is
certainly a new window; an unchanged one proves nothing, since an observed
100 -> 0 reset left it untouched), and everything else falls to a
two-signal test: sustained (>= 2 samples below) AND deep (>= 15 points).
Both cheap, both independent. The failure mode is a bounded under-count,
which costs a missed warning where the over-count cost a false alarm on
every frame. The first sample of a series is a baseline, not burn: seeing
an account already at 40 is not watching it climb there.

### A landing above 100 is not a landing

`lands ~177%` describes nothing. The pool is 100; a projection past it is
a wall plus burn that never happens. The walk now caps the landing at 100
and returns the moment the pool dries, which is the fact worth having —
so the block says `7d dry ~Wed 14:20, 19h before reset; then hard stop`
and the budget line lands on exactly 100. Two readings of one walk.

The walk also stays silent where it has no standing: below two weeks of
history (statusline's floor too, so the surfaces agree about whether a
forecast exists), on a 7d window younger than a day (the profile
describes the windows *before* this one, and the 24h blend describes a day
on the far side of the reset), and on any profile claiming a weekday
averages more than the whole pool per day — no real one can, so that
input came from a broken accountant. It gained the recent-24h blend over
the first day, so a hot streak escalates before the weekday average
catches up.

### One budget line, and every number named

```
budget: ~9 windows left · 6.2%/window stays even · lands ~91% on your pattern
```

`N%/window stays even` is a RATION — spend that per window and the pool
lands exactly on 100. `lands ~N%` is a PREDICTION — spend like you have
been and you end up here. They are different kinds of statement and the
old line ran them together under one word, "heading", which is neither: a
direction is not a destination. The landing now says which model produced
it, `on your pattern` for the learned walk and `at this pace` for the
linear fallback, and linear inherits the same 100 ceiling so the fallback
cannot reintroduce what the walk just lost. The separate `forecast:` line
is gone; there was never a second week to describe.

`~N windows left` no longer counts the window you are standing in. That
one is where you are, not what you have left — the ledger already draws
it as `▮` and the 5h row already prices it, so counting it again made
`▮ + 9` read as ten, and made this tool disagree with statusline about
the same week. Same definition as `windows_ahead` there:
`(7d left - 5h left)`, rounded up. Both clocks tick down together, so the
count holds still inside a window and steps down by exactly one at each
rollover — a countdown rather than a reading that drifts.

### The shared cache has a contract now

`forecast.cache` lives in statusline's store and statusline computes a
superset of it off the same log: the cross-window exchange rate, the
per-model weekly profile, the dollars a quota point costs. ccpace rebuilt
the five fields it knew and wrote them flat over the file, dropping all
three every time it ran — after which statusline's report said "still
learning" about its own price and exchange rate until the next hourly
scan. It also published its pre-envelope profile there, which
statusline's corrupt-profile guard then had to refuse, dropping that
surface back to linear pace as well.

So: the cache carries `schema`, the version of the MODEL rather than of
the file. Writes MERGE and never truncate keys we do not compute. Reads
treat freshness as necessary and not sufficient — an unversioned or
foreign cache is stale however recently it was written — and a fresh
cache of our own schema is read rather than recomputed, because it holds
the same numbers and cost someone else the scan. Written down in
`docs/data.md` and in claude-code-statusline `docs/api/state-dir.md`.

A profile whose only credited day was today is no longer published at
all. It came out as seven `-1`s with `days_history: 0`, harmless to read
and actively harmful to publish: stamped with a current timestamp into a
shared cache, it silenced the walk on every surface that read it until
the hour turned.

### The ledger is one typeface

The strip's baseline was `ˍ` (U+02CD MODIFIER LETTER LOW MACRON) while
every bar above it came from Block Elements. Terminals resolve those
through different faces, so the zero line sat at a different height and
advance width than the bars beside it and the seam showed on every row
that held both. It is `▁` now, the shortest bar of the same run. Burn
starts one rung up, at `▂`; `▅` and above keep their thresholds, so a
fully burned window reads the height it always did.

The ledger's docstring stops claiming its cells are countable as windows
left. They are a grid anchored to the period start — which is what makes
the history readable — while your 5h windows are anchored to the 5h
reset, in phase only by coincidence, and 34 cells span 170 h against a
168 h period besides. Measured across a full week of positions the two
agree about three times in four and are never more than one cell apart.
The sentence owns the number; the row carries the shape.

### Tests

`t/`, run with `make check`. There were none, which is the whole story of
this release: the model was invisible and only its sentences were on
screen, and the sentences were plausible. 38 cases now pin the burn
accounting, the walk's silences and ceilings, the cache contract in both
directions, and the wording of every clause in the budget line.

## v0.2.0 (2026-08-18)

One release for what was found by watching a week of real use against
the CLI's own `/api/oauth/usage` traffic (cctrace, claude-cli 2.1.234).
Nothing here changes what is fetched — the CLI's request is still the
model — only when, from where, and what happens when it fails.

### A failed fetch is a badge, not a lockout

A `429 Retry-After: 3600` used to be taken at face value: the poller
retried inside the hour and, in an intermediate build, wrote an hour-long
cooldown into statusline's `usage.err`, freezing every statusline render
on the machine along with itself. That is not how the CLI treats this
endpoint (it fetches when it needs to and shows an error if that fails),
and a 15-minute poller has no lockout to gain from it. Now: a failure
keeps the last `usage.cache` on screen with `(stale 12m · !429)` on the
account rule, and the next poll is the retry — the footer already counts
it down. With nothing cached at all the frame says
`rate limited (429) (retry in 5m)`, doubling from 60 s and never past
the poll interval, whatever `Retry-After` asked. ccpace never writes
`usage.err`; it never blocks on it either.

### The account is the identity, not the directory

- The bare `.credentials.json` follows statusline's own rule for who it
  is: `STATUSLINE_ACCOUNT`, else deva's `DEVA_AUTH_TAG`
  (`auth-file-<stem>` -> `<stem>`), else the pre-0.18 `DEVA_AUTH_DETAILS`
  stem, else the untagged store root. Same directory as the statusline in
  the same session, so one fetch serves both — inside a deva container as
  well as on a host. It is displayed by that tag, or `default`.
- History (the 5h-window ledger and the forecast) is read from every
  store under the root and partitioned by account uuid, as docs/data.md
  always required of readers. Where a sample landed depends on who
  fetched it (untagged statusline -> root, tagged container ->
  `accounts/<tag>/`, older ccpace -> `accounts/<alias>/`); the ledger
  went blank whenever ccpace looked in the wrong one. Parsed stores are
  cached per (mtime, size) so watch frames do not re-read 20 MB.
- `.credentials.work.json` (the README's `.credentials*.json` glob) used
  to alias to `""` — the default account — and share its `usage.cache`,
  so two accounts showed each other's numbers. Both spellings now yield
  `work`; only `.credentials.json` is the default.

### Watch asks only when the answer can have changed

A fixed-interval poll of an idle account is the request that only ever
finds what it already knows. Watch now skips the request when it has
fetched before, no window reset has passed since, and Claude Code has
done nothing since that fetch — read from `~/.claude/history.jsonl` (a
prompt went out) and the statusline's per-session render state (a
frame was drawn, in any container sharing this `~/.claude`). The block
keeps the last numbers with `(idle 12m)` on its rule; `r` asks anyway.
Unknown activity (no such files) never blocks a fetch.

### The ledger reads as days, and never loses its first window

A thin gap now sits at each local midnight, in history only, in the window ledger
(`▅▁▂ ▃▅ˍ▃▅ ▃▃▁▂▁ …▮ ▯▯`), so days read as clusters — and a day that
held five windows shows it — without a ruler; the same rhythm as
claude-code-statusline's new week row, which draws this ledger from the
same log. And the period start is now snapped to the same 5-min grid the
window keys use: the API jitters `resets_at` by sub-seconds
(15:59:59.76, 16:00:00.47), and a raw `reset - 7d` could land a hair past
slot 0's true start and floor the first window of the week to slot -1 —
silently lost. Row needs 78 columns now (was 72).

### Also

- `--bark` reads the bark CLI's own env: bare `--bark` pushes to
  `BARK_KEY` on `BARK_SERVER` (default api.day.app), and `BARK_GROUP` /
  `BARK_ICON` ride along; `--bark URL` and `CCPACE_BARK` still win.
  (Before: `--bark` without a URL was a usage error, and the env sat
  unread.)
- A notification can never take the watch loop down: `has_command`
  exec'd the shell builtin `command -v` and raised `FileNotFoundError`
  on Linux the first time an event fired without `notify-send`; it uses
  `shutil.which` now, and every channel is fenced — a failing messenger
  is one warning line, not a traceback.
- The ledger's slots ahead are `▯` (the hollow of `▮`: an empty slot),
  not `▫`; same glyph in claude-code-statusline's week row.

### Tier from the credentials file, not the profile

Claude Code persists `subscriptionType` / `rateLimitTier` next to the
token. That is now the tier source: overlaid on the profile every frame,
synthesized into a minimal profile when none has landed, so a 5x -> 20x
upgrade shows on the next frame with no request. The profile is fetched
for the org uuid and subscription dates, 24 h TTL; only a *missing*
profile is retried (every 10 min).

### Watch mode refreshes at the reset boundary, not late

The non-full sleep was always the full poll interval (~15 min), so when
a 5h/7d window rolled over the reset countdown ticked past zero and the
account rendered stale — 100% for a window that had already reset —
until the next interval poll. Watch now caps the sleep at the next
upcoming reset (`+RESET_POLL_GRACE`). `get_earliest_reset` also
considers the model-scoped weekly limits (`limits[].scope`), not just
5h/7d, so the poll targets the window that actually binds.

### A model-scoped 100% counts as full

The watch loop measured fullness with `include_model_specific=False`, so
an account blocked on the fable/opus **weekly** limit (5h and 7d-all
still low) was treated as not-full: never cached, never reset-watched,
polled blindly every interval. It now counts model-scoped limits.

### Fewer requests

- Prepaid credits: 1 h TTL (a balance only moves on a purchase; spend is
  already in the usage payload's `extra_usage`), skipped outright when
  the payload says `credits_ever_enabled: false`.
- User-Agent pinned to `claude-cli/2.1.234 (external, cli)`; the
  prepaid-credits fetch uses the traced `teleport-org` header set
  (`anthropic-version` + `anthropic-client-platform` +
  `x-organization-uuid`, no `anthropic-beta`).
- `-v` no longer prints the httpcore transport trace; one `httpx` line
  per request. `-vv` keeps everything.

## v0.1.1 (2026-08-06)

### Shared fetch pool with claude-code-statusline

usage.cache and profile.cache are now the pool, not private caches:
ccpace reads a usage.cache younger than 60 s instead of fetching (and
does not re-log the sample — one observation, one record), and
publishes its own fetches back (atomic tmp+rename) so statusline's
next render skips its fetch too. profile.cache shared the same way
(raw profile, mtime TTL, 24 h). Two tools, one API load.

### Forecast honors the access boundary

The weekday projection now stops at the subscription period end when
that lands before the 7d reset — the ledger's `┤`, the budget count,
and the forecast line all describe the same span ("+10% by period
end" instead of a "rest of week" number you cannot spend).

## v0.1.0 (2026-08-06)

First release. Extracted from thevibeworks' internal claudex lab
(claude.py's monitor half, wire-verified against claude-cli 2.1.220);
the chat-client half stays private by design.

### Monitor

- Multi-account fleet view from `~/.claude/.credentials*.json`, sorted
  by tier; per-account splitter-rule headers.
- Dual bars (usage vs window-elapsed), 5h/7d/per-model rows, extra
  usage spend, prepaid credit balance (nonzero only).
- Window ledger: the 7d period as 34 countable 5h cells, burn heights
  from the sample store, `▮` now / `▯` ahead / `×` uncovered / `┤`
  access end. Unknown (`░`) and idle (`·`) stay distinct glyphs.
- Advisor: pace warnings with cap ETAs, windows-left budget, %/window
  to stay even. Budget truncates at the subscription period end
  (derived from the billing anniversary; `~` marks the assumption —
  no API endpoint states renewal or cancellation, re-verified against
  claude-cli 2.1.220's endpoint surface).
- Watch TUI: stable facts left, per-second tickers dimmed right; `r`
  refresh, `q` quit.

### Data (docs/data.md)

- Store v1: statusline-compatible typed JSONL records, epoch
  timestamps, raw API sections verbatim, `source` tag; 32 MiB rotation
  with `.1` backup and mkdir-lock, shared with claude-code-statusline.
- Account-scoped dirs keyed by credential alias; readers partition by
  `user.uuid`.
- Logging is on by default (`--no-log` to disable): history is what
  forecasts eat.

### Forecast

- Weekday burn signature from your own samples: positive 7d deltas per
  local day, EWMA-weighted (14-day half-life), projected over the rest
  of the window. Silent below 3 days of history.
- Same model and cache shape as statusline's forecast.cache: two
  surfaces, one set of numbers.

### Notifications

- Events: threshold, full, delta, pace, reset.
- Channels: system notify (macOS/Linux), ntfy (verified round-trip),
  bark (implemented, untested against a real device), custom script
  (JSON on stdin). Severity maps per channel.

### Rate-limit hygiene

- 15 min default poll, 60 s floor, ±10% jitter; exponential backoff
  (60 s → 15 min cap) when all accounts fail; accounts at 100% are not
  polled until reset; credits 5 min TTL with retry-after backoff;
  profile 24 h TTL.

### Plugin

- Installable as a Claude Code plugin: `bin/ccpace` on PATH, `/ccpace`
  skill, self-marketplace manifest.
