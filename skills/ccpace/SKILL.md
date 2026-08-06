---
name: ccpace
description: Check Claude subscription usage, quota pace, and window budget. Use when the user asks how much quota is left, whether they are burning too fast, when limits reset, or to watch usage.
---

# ccpace

Run the monitor and interpret its output for the user.

```
"${CLAUDE_PLUGIN_ROOT}/bin/ccpace"          # one glance, all accounts
"${CLAUDE_PLUGIN_ROOT}/bin/ccpace" --raw    # raw JSON when you need numbers
```

Reading the output:

- Each account block: header rule with tier + alias + subscription period
  end; one row per rate-limit window (5h, 7d, per-model).
- Bars: `█` both usage and time passed, `▓` usage ahead of time (hot),
  `▒` time ahead of usage (headroom), `░` untouched.
- The ledger strip under the 7d row: one cell per 5h window; `▮` = now,
  `▫` = future window, `×` = window the pool will not cover, `┤` = access
  ends there. Countable: cells from `▮` right equal "windows left".
- `!` lines are pace warnings; the budget line gives windows left and
  %/window to stay even; the forecast line projects the week from the
  account's own history.

Answer with the numbers, not the raw dump: utilization, time to reset,
windows left, and whether the pace warning fired. Never run `--watch`
from here (interactive TUI; needs a real terminal).
