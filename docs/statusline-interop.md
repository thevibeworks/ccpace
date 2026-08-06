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
