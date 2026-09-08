# Contributing

For a bug, include the ccpace version, operating system, terminal size,
command, expected result, and actual result. Use `--calendar --demo` when
it reproduces the issue. For a feature, describe the usage decision it
would help someone make.

Do not attach credentials, raw usage stores, account identities, or session
logs. Reduce data problems to synthetic observations before sharing them.

```sh
make check
make demo
make build
```

Tests must isolate the shared usage store with `CCPACE_DATA_DIR`. Changes to
forecasts or quota interpretation need a regression test showing the
failure. Calendar changes should be checked at 80x24 and a wide terminal,
with light and dark palettes. Regenerate screenshots only from demo data.

The Claude collector is `ccpace.py`; provider discovery, account identity,
and Codex OAuth reads are in `ccpace_providers.py`. Calendar evidence and warning state
live in `ccpace_calendar.py`; Textual rendering lives in `ccpace_tui.py`.
Keep shared observations and forecast contracts compatible with
claude-code-statusline. Do not infer account identity from directory names.
