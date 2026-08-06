# ccpace data contract (store v1)

The store is the product's foundation: every forecast, ledger cell, and
notification derives from it. This file is the contract; code that
disagrees with it is wrong.

## Design rulings

- One shared store, two writers. ccpace adopts the record shape that
  thevibeworks/claude-code-statusline already writes (typed JSONL,
  epoch timestamps, top-level window sections). No second dialect:
  claudex's old `{ts,label,usage:{...}}` shape is read-compatible but
  never written.
- Raw API responses are the payload; derived numbers (pace, forecast)
  live in caches, never in the log. A log you can replay beats a log
  you must trust.
- Unknown is not zero. Gaps in the record render as unknown, and the
  forecast refuses to speak below its minimum history.

## Layout

    $CCPACE_DATA_DIR/                 default: ~/.claude/statusline
      accounts/<account>/             account scope (see identity below)
        usage.jsonl                   append-only samples (this spec)
        usage.jsonl.1                 single rotation backup
        profile.cache                 raw /api/oauth/profile response + fetched_at
        prepaid_credits.cache         raw prepaid credits response + fetched_at
        forecast.cache                derived weekday burn profile (rebuildable)

The default root deliberately equals statusline's home: same machine,
same account, one history. Override with CCPACE_DATA_DIR only to
isolate (tests, exotic setups).

### Account identity

`<account>` is the credential alias: the filename stem of the
credentials file (`work.credentials.json` -> `work`). Statusline
scopes by `STATUSLINE_ACCOUNT` / `DEVA_AUTH_TAG` (`auth-file-<stem>`);
the stem is the shared key. ccpace reads both `accounts/<alias>/` and
`accounts/auth-file-<alias>/`, writes to `accounts/<alias>/`.
Cross-account mixing is the classic corruption here (statusline
observed 9000%/day burn rates); readers MUST partition by
`user.uuid` when aggregating, not trust directory placement alone.

## usage.jsonl records

One JSON object per line. `type` discriminates; readers skip unknown
types (forward compatibility). `timestamp` is unix epoch seconds (int).

### type: "usage" — a sample

    {
      "type": "usage",
      "timestamp": 1754870000,
      "source": "ccpace/0.1.0",          // writer + version; statusline omits
      "session_id": null,                 // statusline sets; ccpace watch has none
      "user": {
        "email": "...", "name": "...", "uuid": "...",
        "display_name": "...",
        "subscriptions": {"claude_pro": false, "claude_max": true}
      },
      "organization": {
        "name": "...", "type": "claude_max",
        "billing_type": "stripe_subscription",
        "rate_limit_tier": "default_claude_max_20x"
      },
      "five_hour":  {...},                // raw API section, verbatim
      "seven_day":  {...},                // raw API section, verbatim
      "seven_day_opus": {...},            // legacy section when present
      "extra_usage": {...},               // raw API section, verbatim
      "limits": [...],                    // raw API array, verbatim
      "model": null,                      // statusline sets from session
      "predicted_end": null               // statusline's walk; ccpace omits
    }

ccpace additions are additive only (`source`); it never renames or
re-nests statusline fields. `cached` responses (served from the
100%-cap cache) are NOT logged — the log records observations, not
echoes.

### type: "session_start" / "session_end" — statusline's markers

    {"type":"session_start","session_id":"...","timestamp":...,
     "five_hour_window_end":"...","seven_day_window_end":"..."}
    {"type":"session_end","session_id":"...","timestamp":...}

ccpace never writes these (no session), always tolerates them.

## Rotation

32 MiB cap (USAGE_LOG_MAX_BYTES), single `.1` backup, mkdir-based
lock (`usage.jsonl.rotate.lock`) — identical to statusline so either
writer can rotate without eating the other's history. Readers read
`.1` then current.

## Caches (derived, disposable)

- profile.cache: raw profile + `fetched_at`. TTL 24h.
- prepaid_credits.cache: raw response + `fetched_at`. TTL 5 min.
- forecast.cache: `{computed_at, days_history, recent_24h, recent_48h,
  weekday_profile:{"0".."6"}}` — statusline's shape; ccpace computes
  the same numbers from the same log so the two surfaces cannot
  disagree. Rebuild at most hourly. Unknown weekday = -1.

## Shared fetch pool

usage.cache and profile.cache are not private caches — they are the
pool. Whichever tool fetched last serves both:

- Before fetching usage, read usage.cache; a `fetched_at` younger than
  60 s IS the fetch. Samples served from the pool are not re-logged
  (the fetcher already logged them — one observation, one record).
- After a successful fetch, publish it: raw response + `fetched_at`,
  written atomically (tmp + rename) so a concurrent reader never sees
  a torn file. Same for profile.cache (raw profile, mtime is the fetch
  time, 24 h TTL — statusline's rule).
- Locks (`usage.lock` etc.) are advisory between statusline processes;
  cross-tool safety comes from atomic rename, and the worst race costs
  one duplicate fetch, never a corrupt cache.

## Fetch discipline (rate-limit hygiene)

Defaults chosen so a fleet of watchers stays invisible to the API:

- usage poll: 900 s default, minimum 60 s enforced, ±10% jitter per
  cycle (fleet watchers must not synchronize).
- accounts at 100%: no polling until the earliest reset (cap cache).
- 429/5xx: honor Retry-After when present, else exponential backoff
  60 s -> 120 -> 240 capped at 900 s; failures never tighten the loop.
- profile: fetched once per watch start, then 24 h TTL.
- prepaid credits: 5 min TTL, same backoff file discipline as
  statusline (`prepaid_credits.err` with retry-until epoch).

## Forecast inputs

The weekday model consumes only `type:"usage"` records: positive
deltas of `seven_day.utilization` per local calendar day, partitioned
by `user.uuid`, EWMA-weighted with a 14-day half-life (plan changes
rescale percentages; old scales must fade). Below 3 days of history
the forecast is silent — a model with no data is decoration.
