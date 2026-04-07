"""HTML → PDF conversion using Playwright headless browser."""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

logger = logging.getLogger(__name__)


async def html_to_pdf(html_path: str, pdf_path: str | None = None) -> str | None:
    """Convert an HTML file to PDF using Playwright Chromium.

    Args:
        html_path: Path to the source HTML file.
        pdf_path: Output PDF path. Defaults to same directory as HTML with .pdf extension.

    Returns:
        Path to the generated PDF, or None on failure.
    """
    html_file = Path(html_path)
    if not html_file.exists():
        logger.warning("html_to_pdf: source not found: %s", html_path)
        return None

    if pdf_path is None:
        pdf_path = str(html_file.with_suffix(".pdf"))

    try:
        from playwright.async_api import async_playwright

        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            page = await browser.new_page()

            # file:// URL로 로컬 HTML 열기
            file_url = html_file.resolve().as_uri()
            await page.goto(file_url, wait_until="networkidle")

            await page.pdf(
                path=pdf_path,
                format="A4",
                print_background=True,
                margin={"top": "15mm", "right": "15mm", "bottom": "15mm", "left": "15mm"},
            )

            await browser.close()

        logger.info("html_to_pdf: generated %s", pdf_path)
        return pdf_path

    except Exception as e:
        logger.error("html_to_pdf failed: %s", e)
        return None


def html_to_pdf_sync(html_path: str, pdf_path: str | None = None) -> str | None:
    """Sync wrapper for html_to_pdf."""
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(html_to_pdf(html_path, pdf_path))

    # Already in async context — run in thread
    import concurrent.futures
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(asyncio.run, html_to_pdf(html_path, pdf_path))
        return future.result(timeout=60)
