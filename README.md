# ccpace

Claude usage calendar for your terminal. See your 5h limit, weekly pool,
and model-scoped limits together, with forecasts and history from your own usage.

[![PyPI](https://img.shields.io/pypi/v/ccpace)](https://pypi.org/project/ccpace/)
[![Tests](https://github.com/thevibeworks/ccpace/actions/workflows/check.yml/badge.svg)](https://github.com/thevibeworks/ccpace/actions/workflows/check.yml)
[![MIT](https://img.shields.io/badge/license-MIT-green)](LICENSE)

[Website](https://thevibeworks.github.io/ccpace/) · [Install](#start) ·
[Calendar](#calendar) · [Data contract](docs/data.md) · [Changelog](CHANGELOG.md) ·
[For agents](llms.txt)

![ccpace usage calendar in Spectrum](https://raw.githubusercontent.com/thevibeworks/ccpace/main/docs/calendar-previews/calendar-120.png)

Synthetic demo: the 5h allowance is 88% used while the weekly forecast
leaves about 39% unused. The calendar keeps both conditions visible.
Run the same scenario with `uvx ccpace --calendar --demo`.

## Start

Requires [uv](https://docs.astral.sh/uv/), macOS or Linux, and a terminal.
Python 3.11 or later is resolved by uv.

```sh
uvx ccpace --calendar --demo   # try it without a Claude account
uvx ccpace --calendar          # your accounts, after claude login
uvx ccpace                    # compact one-shot view
uvx ccpace --watch            # compact watch view
```

Upgrade an existing uv installation with `uv tool upgrade ccpace`, or run
`uvx --refresh ccpace --calendar`. The calendar is opt-in.

Claude Code credentials are discovered from `~/.claude/.credentials*.json`.
Both `.credentials.work.json` and `work.credentials.json` name an account;
`-f PATH` selects explicit credential files. A subscription login is needed
for live usage. API-key billing and Codex collection are not supported.

## Calendar

- **Current limits stay visible.** 5h, aggregate 7d, and scoped weekly
  allowances are separate counters with their own reset times.
- **Browse the week.** Interval totals and hourly patterns show where usage
  accumulated. Enter opens hourly detail; History lists quota periods.
- **Forecast from your history.** The same model as claude-code-statusline
  learns weekday and hourly burn. A short history uses a labeled linear
  fallback; the learned forecast requires at least 14 days of history.
- **Warnings without execution control.** Alerts record condition changes,
  and existing notification channels can carry them elsewhere. ccpace never
  pauses, launches, switches models, or steers an agent.

Unavailable cells stay blank. Selecting one explains whether observations
are missing, a forecast is unavailable, or the next quota period has yet
to begin. Observed zero is `0.0`; `+` means a partial observed amount;
`~` marks a forecast; `|` marks a quota reset. Long gaps are not assigned
to individual hours, and forecasts end at the current pool or access boundary.

Spectrum uses mint for usage, cyan for forecasts, and rose for model
identity. Amber and red remain pressure signals. Quiet and Paper are also
available; `NO_COLOR` is honored.

```sh
ccpace --calendar --theme spectrum
ccpace --calendar --theme quiet
ccpace --calendar --theme paper
```

The theme picker and `Ctrl+t` change palettes during a run.
`CCPACE_THEME` sets the default. Light terminal backgrounds are detected
through `COLORFGBG` when it is available.

| Action | Key |
| --- | --- |
| Select an interval | Arrow keys |
| Hourly detail / back | Enter / Escape |
| Previous / next week | `[` / `]` |
| Today | `t` |
| Calendar / History / Alerts | `1` / `2` / `3` |
| Next account / meter | `a` / `m` |
| Acknowledge selected alert | `x` |
| Refresh / quit | `r` / `q` |

Mouse selection and scrolling work too. Compact terminals keep the calendar
and move details below it; narrow terminals use a daily agenda.

Demo scenarios: `mixed`, `weekly`, `scoped`, `stale`, `cold`, `reset`,
`credits`, `rebase`, and `weekly-only`. `d` cycles scenarios; `r` advances
the synthetic clock five minutes. Demo mode reads no credentials, makes
no provider requests, writes no usage or alert state, and sends no notifications.

## Notifications

```sh
ccpace --calendar --ntfy https://ntfy.sh/your-topic
ccpace --calendar --bark https://api.day.app/YOUR_KEY
ccpace --calendar --notifier ./notify-usage.sh
```

These options also work with `--watch`. Bare `--bark` uses `BARK_KEY` and
`BARK_SERVER`. Environment equivalents: `CCPACE_NTFY`, `CCPACE_BARK`,
`CCPACE_NOTIFIER`, `CCPACE_INTERVAL`, `CCPACE_THRESHOLD`, and `CCPACE_TZ`.

Custom notifiers receive JSON on stdin with `id`, `event`, `account`, and
`data`. Calendar events add stable condition and transition IDs, provider,
meter, observation time, and forecast provenance. Forecast notices require
two distinct observations; cap notices are immediate. A reset clock passing
does not establish recovery: a fresh observation must confirm it.

Calendar alert state is bounded to 200 events in `calendar-alerts.json`.
Acknowledgement marks a reviewed event without clearing its condition.
Delivery marked `attempted` does not prove receipt by an agent or device.
Weekly underuse thresholds are experimental: 20 points to enter, 15 to clear.

## Data and limits

Usage comes from the same undocumented OAuth endpoints Claude Code uses,
not transcript token estimates. Samples and fetch caches are shared with
[claude-code-statusline](https://github.com/thevibeworks/claude-code-statusline)
under `~/.claude/statusline`. History is partitioned by account UUID;
directory placement alone is not identity. The calendar requires a known
account UUID before displaying history.

`CCPACE_DATA_DIR` relocates the store; `--no-log` disables usage-sample
logging. Derived caches and calendar alert state still update. Fetching
uses the shared cache, activity gating, and reset boundaries; errors retain
the last observation with its age. The default interval is 15 minutes,
with jitter and a 60-second minimum.

Subscription percentages are not interchangeable credit balances. A model
can consume both its scoped allowance and the shared limits. A 5h cap does
not imply a depleted week, and a scoped cap does not imply every model is
blocked. Paid continuation and session-specific modes require their own
evidence. ccpace does not promise capacity or a particular continuation path.

Provider endpoints can change. Forecasts are estimates. API requests are
read-only except expired-token refresh, which writes the refreshed OAuth
token back to the credentials file. Local usage data stays local; enabled
notifications send messages to the destinations you configure.

## Claude Code plugin

```text
/plugin marketplace add thevibeworks/ccpace
/plugin install ccpace@ccpace
```

The `/ccpace` skill reports usage in a conversation. Interactive calendar
and watch views run in a separate terminal. The compact monitor can also
run from a downloaded `ccpace.py`; the calendar needs the full package or checkout.

## Verify and contribute

```sh
git clone https://github.com/thevibeworks/ccpace
cd ccpace
make check
make demo
make build
```

Tests cover quota accounting, forecast boundaries, account isolation,
notification transitions, and calendar navigation at 50, 80, 120, and 160
columns. Test data is synthetic and isolated from the real usage store.
These checks validate behavior, not forecast accuracy on every workload.

The terminal UI uses [Textual](https://textual.textualize.io/). Collection
uses [HTTPX](https://www.python-httpx.org/). The shared store and forecast
contract are developed alongside claude-code-statusline.

[Contributing](CONTRIBUTING.md) · [Calendar design](docs/calendar-tui.md) ·
[Theme previews](docs/calendar-previews/README.md) · [Data contract](docs/data.md)

MIT. Unofficial; not affiliated with Anthropic.
