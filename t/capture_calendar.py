# /// script
# requires-python = ">=3.11"
# dependencies = ["httpx[socks]", "textual>=8.2,<9", "cairosvg", "pillow"]
# ///
"""Render synthetic calendar previews. Run with uv run --script, --output DIR."""

import argparse
import asyncio
import os
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

import cairosvg
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from ccpace_calendar import DemoSource  # noqa: E402
from ccpace_tui import CalendarApp  # noqa: E402


async def capture(output):
    output.mkdir(parents=True, exist_ok=True)
    os.environ.pop("NO_COLOR", None)
    for name, size, theme in [
        ("calendar-120", (120, 36), "spectrum"),
        ("calendar-quiet", (120, 36), "quiet"),
        ("calendar-paper", (120, 36), "paper"),
        ("calendar-80", (80, 24), "paper"),
        ("calendar-50", (50, 24), "spectrum"),
        ("calendar-50-quiet", (50, 24), "quiet"),
        ("calendar-50-paper", (50, 24), "paper"),
    ]:
        app = CalendarApp(DemoSource(), theme=theme)
        async with app.run_test(size=size) as pilot:
            for _ in range(100):
                await pilot.pause(0.05)
                if app.snapshots and not app.loading:
                    break
            assert app.snapshots and not app.load_error
            await pilot.pause()
            document = ET.fromstring(app.export_screenshot())
            # Cairo does not perform browser-style fallback for missing glyphs.
            style = ET.SubElement(document, "{http://www.w3.org/2000/svg}style")
            style.text = (
                ".rich-terminal text {font-family: 'JetBrains Mono', monospace;}"
            )
            path = output / f"{name}.png"
            cairosvg.svg2png(bytestring=ET.tostring(document), write_to=str(path))
            image = Image.open(path).convert("RGB")
            center = image.crop((20, 80, image.width - 20, image.height - 40))
            assert len(center.getcolors(center.width * center.height)) > 100, (
                "blank or unrendered terminal"
            )
            print(path)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    asyncio.run(capture(parser.parse_args().output))
