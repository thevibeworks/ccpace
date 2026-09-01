# Proposal: shared usage store for thevibeworks Claude tools

Status: proposed to thevibeworks/claude-code-statusline (2026-08-06).
ccpace already implements its side; nothing here breaks statusline as
shipped today.

## Problem

Two tools observe the same account state: statusline samples usage on
every Claude Code session render; ccpace samples it from a standalone
watch loop. Separate stores would mean half-blind forecasts in both
tools, doubled API fetches, and two dialects of the same record.

## Proposal

1. Declare `~/.claude/statusline/` (under `CLAUDE_CONFIG_DIR` when set)
   the **shared usage store** for thevibeworks Claude tools, and amend
   the state-dir contract from "statusline is the only writer" to a
   record contract any writer must honor:
   - JSONL records typed with `type`; readers skip unknown types.
   - `type:"usage"` records carry epoch `timestamp`, raw API sections
     verbatim (`five_hour`, `seven_day`, `extra_usage`, `limits`), and
     `user.uuid` for partitioning. Writers other than statusline set
     `source: "<tool>/<version>"`.
   - Rotation: 32 MiB cap, single `.1` backup, `usage.jsonl.rotate.lock`
     mkdir-lock. Readers read `.1` then current.
   - Derived caches (`forecast.cache`, `prepaid_credits.cache`,
     `profile.cache`) keep their current shapes; any tool may rebuild
     them, TTLs as today (forecast 1h, credits 5min, profile 24h).
   - "Any tool may rebuild them" was too loose for `forecast.cache`, the
     one of the three that carries a learned MODEL rather than a cached
     response. It now carries `schema`, and a co-writer must stamp the
     schema it implements, merge rather than replace, and treat a
     foreign or unversioned cache as stale however fresh its timestamp.
     Read the full contract in claude-code-statusline
     `docs/api/state-dir.md`. Until this was written down, ccpace
     rebuilt the five fields it knew and dropped statusline's exchange
     rate, per-model profile and price join every time it ran — and
     published a profile from a burn model statusline had already
     abandoned, which statusline's own guard then had to refuse.
   - `hour_profile` is part of that contract as of ccpace 0.4.0 /
     statusline 0.36.0. It is 24 burn MULTIPLIERS by local hour,
     `{"0": 0.10, ..., "23": 1.83}`, mean 1.0 — the instantaneous rate at
     hour h is `weekday_rate * mult[h]`, so integrating a whole local day
     reproduces the weekday total exactly. Both writers compute it and
     both must compute it the same way, because either may rebuild the
     cache first:

       * credit each envelope delta to its local `(day, hour)`;
       * exclude today, EWMA-weight the rest at the 14-day half-life
         (the weekday constants, unchanged);
       * `share[h] = w_burn[h] / total_w_burn`, published only when
         `total_w_burn > 0` — no extra day gate, readers already gate on
         `days_history >= 14`;
       * `m[h] = max(share[h] * 24, 0.1)`, then scale so the mean is
         exactly 1, then round to 2 decimals. Floor FIRST: it is the hedge
         for the occasional overnight autonomous run, so a rest hour
         projects a tenth of a uniform hour and never zero. Rounding
         happens at build time so every reader sees one set of numbers.

     Reading it: use it only when all 24 keys are present, every value is
     numeric in [0, 24], and the mean is in [0.9, 1.1]. Absent or invalid
     means flat — a multiplier of 1 everywhere, which is the behaviour
     before the field existed. A bad hour shape never silences a forecast;
     only the weekday guards do that.

     `REST_MULT_MAX = 0.25` is the shared reading rule: an hour whose
     multiplier is below it is a REST hour. Both surfaces count the
     windows you are awake for over the same span (from the end of the
     live 5h window to the end of the 7d window), ceil the waking seconds
     into 5h windows, and clamp to `windows_ahead`. The number appears
     beside the window count on both, and two surfaces that disagree about
     how much of the week you are awake for is the same failure as
     disagreeing about how much of it is left. `schema` stays 2: the
     model of the existing fields did not change.

     `REST_SLOT_AWAKE_MIN_SECS = 9000` is the second reading rule off the
     same field (ccpace 0.6.0 / statusline 0.38.0): a 5h slot on either
     tool's 7d grid is a NIGHT when under half of it — half a window — is
     waking seconds by the rule above, measured across the slot's whole
     wall span rather than the hour it opens in. Both tools draw such a
     slot as the same hollow ▯ in a dim tint; neither invents a glyph, and
     neither lets a dry projection overwrite a cell (v0.7.0 / statusline
     v0.39.0: a future cell is a slot, never a verdict — the wall belongs
     to the advice/notice sentence; statusline's folded token alone still
     carries the `×` mark). Unlearned by
     the read validation above, or short of `days_history >= 14`, and the
     rows draw exactly as they did before the rule existed. The dim cells
     and the `~N awake` count may differ by one: the grid is anchored to
     the period start and the count comes from real clocks — the same
     one-cell tolerance the two surfaces already document for the window
     count itself. `schema` still stays 2: no field moved.

   - Not every shared rule is a field. The SCOPED STRAND reading (ccpace
     0.5.0 / statusline 0.37.0) is computed live off the usage payload and
     touches `forecast.cache` not at all. The account's 7d pool and a
     model-scoped weekly pool count from the SAME reset instant, so the
     live ratio between them IS this week's mix rate — scoped points per
     7d point — and the closed forms need no history:

       mix       = scope / seven
       reachable = round((100 - seven) * scope / seven)
       strand    = round(100 * (seven - scope) / seven)
                 = (100 - scope) - reachable

     Live check: seven 81, scope 63 -> mix 0.78, reachable 15, strand 22;
     corpus mining of the same week put dF/dS at 0.77, so the ratio is the
     estimator rather than a stand-in for one. Both tools gate it on the
     same reading rules, and the gates ARE the contract: same wall
     (`|scope reset - 7d reset| <= 120 s` — Anthropic could split them
     someday), `SCOPE_MIX_MIN_7D = 60`, `SCOPE_MIX_MIN_SCOPE = 5`, neither
     pool at 100, `SCOPE_STRAND_MIN_PCT = 10`, and a 7d window past the
     young guard. statusline mutes it additionally while a rebase is
     newsworthy and while its own scoped walk already projects the scoped
     cap arriving first — "caps first" and "strands" cannot both be true in
     one frame. ccpace has neither mechanism and does not invent one.

     The pure-scope coupling — what a scoped point costs the account when
     only that model runs — is deliberately NOT published on either side:
     n=22 and a band wide enough to be fluent and wrong.

2. Account scoping: statusline keys `accounts/<tag>` by
   `STATUSLINE_ACCOUNT`/`DEVA_AUTH_TAG` (`auth-file-<stem>`); ccpace
   keys by the credential filename stem. The stem is the shared key —
   ask: statusline additionally accepts the bare stem as a tag so both
   tools land in one directory per account (ccpace already reads both
   spellings).

3. Rate-limit hygiene is part of the contract: TTL-gate every fetch,
   honor Retry-After, back off exponentially, never poll accounts
   pinned at 100% before their reset.

## Why not a new `~/.claude/thevibeworks/` vendor dir

Evaluated and rejected for now: `~/.claude/statusline/` is a published
contract (docs/api/state-dir.md, usage-insight skill hardcodes it), the
data is account-scoped and must travel with the credential overlay
(deva bind-mounts), and `${CLAUDE_PLUGIN_DATA}` is per-plugin and
deleted on uninstall — wrong lifetime for shared history. If a third
tool ever needs the namespace, revisit with a migration + compat
symlink; statusline already has migration machinery.

## As a plugin (statusline)

Plugins cannot set the main `statusLine` (plugin settings.json supports
only `agent`/`subagentStatusLine`), so plugin-izing statusline means:
plugin distributes the script + skills; an install skill copies
`statusline.sh` to the stable `~/.claude/statusline.sh` and patches
user settings — never point settings at `${CLAUDE_PLUGIN_ROOT}` (the
path dies ~14 days after each update). ccpace ships this pattern's
read-only sibling already (`bin/` shim + skill).
