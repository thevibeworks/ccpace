"""Generate stable agent indexes from the package and its maintained docs."""

from pathlib import Path
import argparse
import tomllib

ROOT = Path(__file__).resolve().parent.parent
PAGES = (
    "README.md",
    "docs/calendar-tui.md",
    "docs/data.md",
    "DESIGN.md",
    "CONTRIBUTING.md",
    "CHANGELOG.md",
)


def artifacts():
    package = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]
    repository = package["urls"]["Repository"]
    lines = [
        f"# {package['name']}",
        "",
        f"> {package['description']}.",
        "",
        "Install with `uvx ccpace`. `--calendar --demo` needs no account;",
        "`--calendar` requires a Claude subscription login and an interactive terminal.",
        "For an agent tool call, use the one-shot `ccpace` or `ccpace --raw`.",
        "The calendar watches and warns; it does not steer or pause agents.",
        "The package includes the collector, calendar model, and Textual UI.",
        "",
        "## Docs",
        "",
    ]
    for filename in PAGES:
        source = (ROOT / filename).read_text()
        title = source.splitlines()[0].removeprefix("# ")
        paragraphs = source.split("\n\n")
        description = next((p.replace("\n", " ") for p in paragraphs[1:]
                            if p.strip() and not p.startswith(("#", "[", "!["))), title)
        lines.append(f"- [{title}]({repository}/blob/main/{filename}): {description}")
    lines += [
        "",
        "## Optional",
        "",
        "- [Full documentation](https://thevibeworks.github.io/ccpace/llms-full.txt): All mapped documentation in one file",
        "",
    ]
    index = "\n".join(lines)
    full = (
        "\n\n---\n\n".join((ROOT / filename).read_text().rstrip() for filename in PAGES)
        + "\n"
    )
    return {"llms.txt": index, "site/llms.txt": index, "site/llms-full.txt": full}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    for filename, content in artifacts().items():
        path = ROOT / filename
        if args.check:
            if not path.exists() or path.read_text() != content:
                raise SystemExit(
                    f"Stale agent docs: {filename}; run uv run t/build_agent_docs.py"
                )
        else:
            path.write_text(content)


if __name__ == "__main__":
    main()
