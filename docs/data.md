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
      usage.jsonl, *.cache            the untagged account (see identity below)
      accounts/<account>/             tagged / named accounts
        usage.jsonl                   append-only samples (this spec)
        usage.jsonl.1                 single rotation backup
        usage.cache                   raw /api/oauth/usage response + fetched_at
        usage.err                     statusline's fetch-error state (ccpace: read never, write never)
        profile.cache                 raw /api/oauth/profile response (mtime = fetch time)
        prepaid_credits.cache         raw prepaid credits response + fetched_at
        forecast.cache                derived weekday burn profile (rebuildable)

The default root deliberately equals statusline's home: same machine,
same account, one history. Override with CCPACE_DATA_DIR only to
isolate (tests, exotic setups).

### Account identity

`<account>` is the write key, and it is the same key statusline uses
for the same session:

- a named credentials file is its own account: `work.credentials.json`
  or `.credentials.work.json` -> `work` (the part that is not
  "credentials"; only `.credentials.json` is the default);
- the default file (`claude login`, or a runner's overlay) is whoever
  the runner says — statusline's rule, sanitized the same way:
  `STATUSLINE_ACCOUNT`, else `DEVA_AUTH_TAG` (`auth-file-<stem>` ->
  `<stem>`, `auth-default` = none), else the pre-0.18
  `DEVA_AUTH_DETAILS` stem, else untagged -> the store ROOT. It is
  displayed by that tag, or `default`.

Directories are where a sample was WRITTEN, not who it belongs to: the
same account lands at the root when an untagged statusline fetched, in
`accounts/<tag>/` when a tagged container did, in `accounts/<alias>/`
when an older ccpace did. Readers therefore read every store under the
root and partition by `user.uuid`; only without a known uuid (no profile
yet) does ccpace fall back to the account's own directories. Cross-account
mixing is the classic corruption here (statusline observed 9000%/day burn
rates) — never trust placement alone.

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

- profile.cache: raw profile, mtime is the fetch time. TTL 24h. The
  tier chip does not depend on it: `rateLimitTier` / `subscriptionType`
  are read from the credentials file itself (the CLI keeps them there),
  so a plan change shows on the next frame with no request.
- prepaid_credits.cache: raw response + `fetched_at`. TTL 5 min in
  statusline, 1 h in ccpace (a balance only moves on a purchase; spend
  is already in the usage payload). Not fetched at all when the usage
  payload says `extra_usage.credits_ever_enabled: false`.
- forecast.cache: `{schema, computed_at, days_history, recent_24h,
  recent_48h, weekday_profile:{"0".."6"}, ...}` — statusline's shape,
  and statusline computes a SUPERSET off the same log (`pct_per_window`,
  `scoped_*`, `cost`). Rebuild at most hourly. Unknown weekday = -1.

  This is the one derived cache more than one tool wants to write, so
  `schema` versions the MODEL (2 = envelope burn) and the rule cuts both
  ways. **Reading**: freshness is necessary and not sufficient — a cache
  whose schema is missing or lower is rebuilt on sight, however recently
  it was written. **Writing**: stamp the schema you actually implement,
  and MERGE into what is already there. ccpace rebuilt only the five
  fields it knew for months, which silently truncated statusline's
  exchange rate, per-model profile and price join on every run; those
  surfaces then said "still learning" until the next hourly scan. A
  writer owns the keys it computes and nothing else.

  When a fresh cache of our own schema is already there, read it rather
  than recompute: same numbers, one scan. See claude-code-statusline
  `docs/api/state-dir.md`, "The co-writer contract".

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
- usage.err is statusline's own fetch-error cooldown. ccpace neither
  writes it (a `Retry-After: 3600` written there would freeze every
  statusline render on the machine) nor gates on it (a poller on a
  15-min tick has nothing to gain from sitting out an hour, and a
  fresh usage.cache it publishes is served by statusline regardless).
  ccpace's own failures live in memory for the run: the last usage.cache
  is shown at any age with a `(stale <age> · !429)` badge and the next
  poll is the retry.

## Fetch discipline (rate-limit hygiene)

Defaults chosen so a fleet of watchers stays invisible to the API:

- usage poll: 900 s default, minimum 60 s enforced, ±10% jitter per
  cycle (fleet watchers must not synchronize).
- accounts at 100%: no polling until the earliest reset (cap cache).
- any fetch failure (429, 5xx, transport): show the last cache badged
  stale, retry on the next tick. With nothing to show at all: doubling
  backoff from 60 s, stretched to `Retry-After` when sent, never past
  the poll interval — the same posture as the CLI, which retries only a
  401 and otherwise just reports the failure.
- profile: 24 h shared TTL; only a profile that never landed is retried
  (every 10 min). Tier comes from the credentials file, not the profile.
- prepaid credits: 1 h TTL, skipped when credits were never enabled,
  same backoff file discipline as statusline (`prepaid_credits.err`
  with retry-until epoch).

## Forecast inputs

The weekday model consumes only `type:"usage"` records, partitioned by
`user.uuid` and EWMA-weighted with a 14-day half-life (plan changes
rescale percentages; old scales must fade). Below 14 days of history
the forecast is silent — a model with no data is decoration, and 14 is
also statusline's floor, so the two surfaces agree about whether a
forecast exists at all.

Daily burn is **the rise of a monotone envelope**, never the sum of raw
positive deltas. Utilization inside a window only climbs, so a sample
below the running max is one of two things and they need opposite
answers:

| | what it is | what to do |
|---|---|---|
| stale | an idle session reporting the numbers it last saw | hold the envelope |
| reset | the counter really went back to zero | re-baseline, credit nothing |

`resets_at` cannot tell them apart on its own — an observed 7d reset
(100 -> 0) left it untouched — so the window key is a ONE-WAY hint: a
newer key is certainly a new window, an unchanged one proves nothing.
A stale window's samples are dropped; everything else falls to a
two-signal test, sustained (>= 2 samples below) AND deep (>= 15 points).
The failure mode is a bounded under-count, which costs a missed warning;
the over-count cost a false alarm on every frame.

This is not a refinement. Summing raw deltas credits every stale dip and
then credits the re-climb, counting the same burn twice — measured, it
read 146 points of burn against a real 50-point week, and put 149%/day
into a Thursday. The projection built on that said `+133% rest of week`
on a week with 56% of the pool left.

## The projection

One walk, read twice. `project_week` steps from now to the reset (or to
the access boundary, whichever binds) a day at a time against the
weekday profile, blending the last 24h over the first day so a hot
streak escalates before the weekday average catches up. It returns the
landing **capped at 100** and the moment the pool dries, if it does.

The cap is the point. Utilization cannot exceed the pool: a projection
of 177% is not a landing, it is a wall plus burn that never happens.
Above 100 the fact is the DATE, and that is what the block prints — a
`!` wall with the time and the gap before reset — while the budget line
lands on exactly 100. Two readings of one model, never two models.

It stays silent rather than guess: below the history floor, on a window
younger than 24 h (the profile describes the windows *before* this one),
on nothing spent yet, and on any profile claiming a weekday averages more
than the whole pool per day — no real one can, so that input came from a
broken accountant.

## The budget line

Three clauses, and the grammar keeps them apart because two of them are
different kinds of statement:

```
budget: ~9 windows left · 6.2%/window stays even · lands ~91% on your pattern
         runway              RATION                  PREDICTION
```

`N%/window stays even` is what to spend — that rate lands the pool
exactly on 100. `lands ~N%` is where your own behaviour takes you, tagged
`on your pattern` for the learned walk and `at this pace` for the linear
fallback, so the reader always knows which model spoke. The runway counts
windows AHEAD of the one you are in: the current window is where you are,
not what you have left, and it is already drawn as `▮`.
