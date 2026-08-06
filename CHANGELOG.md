# Changelog

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
  from the sample store, `▮` now / `▫` ahead / `×` uncovered / `┤`
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
