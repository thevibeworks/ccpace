# /// script
# requires-python = ">=3.11"
# dependencies = ["playwright"]
# ///
"""Browser checks for the local or deployed static site; captures go outside the repo."""

import argparse
import asyncio
from pathlib import Path

from playwright.async_api import async_playwright


async def check(url, output, executable):
    output.mkdir(parents=True, exist_ok=True)
    async with async_playwright() as p:
        options = {"headless": True}
        if executable:
            options["executable_path"] = executable
        browser = await p.chromium.launch(**options)
        try:
            for width, height in ((1440, 1000), (390, 844)):
                page = await browser.new_page(viewport={"width": width, "height": height})
                errors = []
                page.on("pageerror", lambda error: errors.append(str(error)))
                await page.goto(url, wait_until="networkidle")
                assert await page.locator("h1").inner_text() == "ccpace"
                assert await page.evaluate("document.documentElement.scrollWidth <= innerWidth")
                for palette in ("spectrum", "quiet", "paper"):
                    await page.locator(f'label:has(input[value="{palette}"])').click()
                    await page.wait_for_function("Array.from(document.images).every(i => i.complete && i.naturalWidth > 0)")
                    current = await page.locator("#calendar-image").evaluate("i => i.currentSrc")
                    assert ("50" in current) == (width == 390)
                    await page.screenshot(path=str(output / f"site-{width}-{palette}.png"), full_page=True)
                await page.locator(".copy").click()
                await page.wait_for_function("document.querySelector('#copy-status').textContent.length > 0")
                assert not errors, errors
                print(f"{width}x{height}: images, palettes, copy control, layout, and JS OK")
                await page.close()
        finally:
            await browser.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default=(Path(__file__).resolve().parent.parent / "site/index.html").as_uri())
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--executable")
    args = parser.parse_args()
    asyncio.run(check(args.url, args.output, args.executable))
