#!/usr/bin/env python3
"""Export the intro deck to a 16:9 PDF via headless Chrome."""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

CHROME_NAMES = (
    "google-chrome",
    "google-chrome-stable",
    "chromium",
    "chromium-browser",
    "chrome",
)

SLIDES_DIR = Path(__file__).resolve().parent
DEFAULT_HTML = SLIDES_DIR / "index.html"
DEFAULT_PDF = SLIDES_DIR / "farmnotary.pdf"


def _find_chrome(explicit: str | None) -> str:
    if explicit:
        path = shutil.which(explicit) or explicit
        if Path(path).exists():
            return path
        raise SystemExit(f"chrome not found: {explicit}")
    for name in CHROME_NAMES:
        found = shutil.which(name)
        if found:
            return found
    raise SystemExit(
        "Chrome or Chromium is required. Install one, or pass --chrome PATH."
    )


def export_pdf(html: Path, pdf: Path, chrome: str | None) -> None:
    html = html.resolve()
    pdf = pdf.resolve()
    if not html.is_file():
        raise SystemExit(f"deck not found: {html}")
    pdf.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="farmnotary-chrome-pdf-") as tmp:
        cmd = [
            _find_chrome(chrome),
            "--headless",
            "--no-sandbox",
            "--disable-gpu",
            "--disable-dev-shm-usage",
            "--no-first-run",
            "--no-default-browser-check",
            "--disable-extensions",
            "--disable-background-networking",
            "--disable-component-update",
            "--disable-sync",
            "--hide-scrollbars",
            "--mute-audio",
            "--no-pdf-header-footer",
            "--window-size=1920,1080",
            f"--user-data-dir={tmp}",
            "--virtual-time-budget=8000",
            f"--print-to-pdf={pdf}",
            html.as_uri(),
        ]
        try:
            subprocess.run(cmd, check=True, timeout=60)
        except subprocess.TimeoutExpired:
            if not pdf.is_file() or pdf.stat().st_size == 0:
                raise
    if not pdf.is_file() or pdf.stat().st_size == 0:
        raise SystemExit(f"Chrome did not write a PDF to {pdf}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--html", type=Path, default=DEFAULT_HTML)
    parser.add_argument("--pdf", type=Path, default=DEFAULT_PDF)
    parser.add_argument("--chrome", help="Chrome or Chromium binary")
    args = parser.parse_args(argv)
    export_pdf(args.html, args.pdf, args.chrome)
    print(args.pdf)
    return 0


if __name__ == "__main__":
    sys.exit(main())
