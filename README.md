# ccpace

Pace your Claude quota. Multi-account usage monitor for Claude
subscriptions: real utilization from the official usage endpoint — not
estimates from transcripts — a countable 5h-window budget, weekday
forecasts learned from your own history, and push notifications.

```
── [20x] work · period ends ~Aug 11 ────────────────────────────────
5h     7% █▒░░░░░░░░  3h 48m   @19:00              0.3x
7d     3% █░░░░░░░░░  6d 8h    @Thu 13 00:00       0.3x
fable  3% █░░░░░░░░░  6d 8h    @Thu 13 00:00       0.3x
           ▁▁▂▮▯▯▯▯▯▯▯▯▯▯▯▯▯▯▯▯▯▯▯▯▯▯▯▯┤
           budget: ~23 windows left · 4.2%/window stays even · period ends ~Aug 11
```

## Install

```sh
uvx ccpace                 # one glance, all discovered accounts
uvx ccpace --watch         # live TUI: r=refresh, q=quit
```

Or grab the single file — it is the whole tool:

```sh
curl -fsSLO https://raw.githubusercontent.com/thevibeworks/ccpace/main/ccpace.py
uv run --script ccpace.py
```

As a Claude Code plugin (`/ccpace` inside Claude Code):

```
/plugin marketplace add thevibeworks/ccpace
/plugin install ccpace@ccpace
```

Requires [uv](https://docs.astral.sh/uv/) and a Claude subscription
you are logged into (`claude login`); credentials are discovered from
`~/.claude/.credentials*.json`. Multiple credential files = fleet view,
sorted by tier.

## What it shows

- One block per account: 5h window, 7d window, per-model caps, extra
  usage spend, prepaid credit balance when nonzero.
- Dual bars merge usage with window-elapsed time: `█` both passed, `▓`
  usage ahead (hot), `▒` time ahead (headroom), `░` untouched.
- The window ledger: the 7d period as its 5h slots, one cell each.
  `▂▃▄▅▆▇█` what a slot burned (from your sample history), `▁` the
  baseline (ran, cost under a point — the shortest bar of the same block,
  so the zero line and the bars share one font), `░` unknown, `▮` now,
  `▯` ahead, `×` won't be covered at current pace, `┤` access ends there.
  The cells are a grid anchored to the period start, so read them for
  shape; the budget line's count comes from the clocks.
- The advisor: walls (`!`) and one budget line — windows left, the ration
  that keeps you even, and where the week lands. The landing comes from
  your own weekday profile once there are two weeks of history (`on your
  pattern`), from linear pace before that (`at this pace`). One model per
  block, named, so two numbers on screen never describe the same week
  differently.
- Budget math truncates at the subscription period end (derived from
  the billing anniversary — the API exposes no cancel/renew date, so
  the boundary is assumed and marked with `~`).

## Notifications

System notifications (macOS/Linux) fire on threshold, quota-full,
pace, and reset events. Add push channels:

```sh
ccpace --watch --ntfy https://ntfy.sh/your-topic
ccpace --watch --bark https://api.day.app/YOUR_KEY
ccpace --watch --bark                        # bark CLI env: BARK_KEY on BARK_SERVER
ccpace --watch --notifier ~/bin/my-hook.sh   # JSON on stdin
```

Env: `CCPACE_NTFY`, `CCPACE_BARK`, `CCPACE_NOTIFIER`, `CCPACE_INTERVAL`,
`CCPACE_THRESHOLD`, `CCPACE_TZ` (e.g. `America/New_York,Asia/Tokyo`).
Bare `--bark` reads the bark CLI's own `BARK_KEY` / `BARK_SERVER`
(default `api.day.app`), and `BARK_GROUP` / `BARK_ICON` ride along when set.

## Data

Samples append to a shared store compatible with
[claude-code-statusline](https://github.com/thevibeworks/claude-code-statusline)
(`~/.claude/statusline/accounts/<alias>/usage.jsonl`): both tools feed
one history, so the ledger and forecasts get richer whichever tool you
run. Contract in [docs/data.md](docs/data.md). `--no-log` disables
writing; `CCPACE_DATA_DIR` relocates the store.

## Honest caveats

- Uses the same undocumented OAuth endpoints as the Claude Code CLI,
  read-only, against your own account. Anthropic can change or gate
  them at any release; expect breakage, report it, don't build a
  business on it.
- One deliberate write: expired tokens are refreshed via the official
  OAuth flow and written back to the credentials file — the same thing
  Claude Code does on your behalf.
- Polling asks only when the answer can have changed: one fetch pool
  shared with claude-code-statusline (same account, same directory, one
  request serves both); in watch mode an account is not re-fetched while
  Claude Code has done nothing since the last fetch (its history and
  statusline session state, across every container sharing `~/.claude`)
  and no window has reset — the block says `(idle 12m)`, `r` asks anyway.
  Reset boundaries wake the loop; the 15 min interval (± jitter, min 60 s)
  is the ceiling. A failed fetch keeps the last good numbers on screen,
  badged `(stale 12m · !429)`, and the next poll is the retry — nothing
  is locked out.
- The grammar — rows, ledger, provenance, requests — is one page:
  [DESIGN.md](DESIGN.md).
- Forecasts are your own history extrapolated, not a promise. Below two
  weeks of samples the learned walk stays silent and the line falls back
  to linear pace, saying which one spoke. It also stays silent on a 7d
  window younger than a day, and on a profile whose numbers are
  impossible — a projection you cannot check is worse than none.
- Not affiliated with Anthropic.

## Development

```sh
make check    # the test suite
make run      # this tree, once, against your real accounts
make build    # wheel + sdist
```

Tests use their own `CCPACE_DATA_DIR`; nothing in `t/` touches the real
store. The suite is where the burn model lives in readable form — if you
change how burn is counted, that is the file to argue with first.

## License

MIT
