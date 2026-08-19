# Changelog

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
