# ccpace — dev tasks.
#
#   make check    the test suite (what CI runs, and what you run before a tag)
#   make run      this tree against your real accounts, once
#   make build    wheel + sdist into dist/
#
# Everything goes through uv: ccpace.py declares its own dependencies and
# `uv run` resolves them, so there is no venv to activate and no way for the
# tree you tested to differ from the tree you shipped.

SHELL := /bin/bash
.DEFAULT_GOAL := help

UV := uv

.PHONY: help check test run watch demo demo-grid demo-timeline agent-docs previews build clean

help:
	@echo "ccpace"
	@echo
	@echo "  make check     pytest t/"
	@echo "  make run       run this tree once (real accounts)"
	@echo "  make watch     run this tree in watch mode"
	@echo "  make demo      usage calendar, synthetic data"
	@echo "  make agent-docs  refresh the public agent indexes"
	@echo "  make previews  regenerate synthetic screenshots and site assets"
	@echo "  make build     wheel + sdist into dist/"
	@echo "  make clean     remove dist/ and caches"

check: test

test:
	@$(UV) run --group dev pytest t/ -q

run:
	@./bin/ccpace

watch:
	@./bin/ccpace --watch

demo:
	@./bin/ccpace --calendar --demo

demo-grid demo-timeline: demo

agent-docs:
	@$(UV) run t/build_agent_docs.py

previews:
	@$(UV) run --script t/capture_calendar.py --output docs/calendar-previews
	@mkdir -p site/assets
	@cp docs/calendar-previews/*.png site/assets/

build: check
	@$(UV) build

clean:
	@rm -rf dist build .pytest_cache __pycache__ t/__pycache__
