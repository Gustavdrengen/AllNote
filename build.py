#!/usr/bin/env python3
"""AllNote: content HTML -> print-ready A4 PDF.

The HTML files under `content/` are *content only*. This script injects
`factsheet.css`, a per-subject accent colour and the page footer (subject ·
title, page x / y) into a copy held in memory, then prints it with headless
Chromium. Nothing about the layout lives in the HTML, so every PDF can be
regenerated after a styling change:

    python build.py            # rebuild what changed
    python build.py --force    # rebuild everything
    python build.py Matematik  # one subject

File layout (reserved, do not invent other shapes):

    content/Matematik/Retvinklede_trekanter.html   <- written by the agent
    dist/Matematik/Retvinklede_trekanter.pdf       <- written by this script
"""

from __future__ import annotations

import argparse
import hashlib
import io
import os
import re
import sys
import time
from dataclasses import dataclass
from html import escape
from pathlib import Path

ROOT = Path(__file__).resolve().parent
CSS_FILE = ROOT / "factsheet.css"
BUILD_FILE = Path(__file__).resolve()
CONTENT_DIR = ROOT / "content"
DIST_DIR = ROOT / "dist"

# Folders that are never subject folders.
SKIP_DIRS = {"tools", "node_modules", "__pycache__", ".git", ".freebuff", ".cloud-attachments"}

# A sheet longer than this is usually padded, not dense.
MAX_PAGES = 3

# Chromium print settings. Thin margins, no background ink.
PRINT = dict(
    format="A4",
    print_background=False,
    scale=1.0,
    prefer_css_page_size=False,
    margin={"top": "7mm", "bottom": "10mm", "left": "8mm", "right": "8mm"},
)

# Fixed accent per subject; unknown subjects get a stable colour from a hash.
ACCENTS = {
    "Matematik": "#1c5f9e",
    "Fysik": "#6a3d9a",
    "Kemi": "#0f6f66",
    "Biologi": "#2f7d32",
    "Bioteknologi": "#2f6f5a",
    "Naturgeografi": "#4a6b2a",
    "Geografi": "#36618e",
    "Astronomi": "#2c3e6b",
    "Dansk": "#8a3348",
    "Engelsk": "#8a3348",
    "Tysk": "#8a3348",
    "Fransk": "#8a3348",
    "Spansk": "#8a3348",
    "Latin": "#8a3348",
    "Historie": "#8a5a2b",
    "Oldtidskundskab": "#8a5a2b",
    "Samfundsfag": "#2f5d7c",
    "Erhvervsøkonomi": "#4a6142",
    "Filosofi": "#5b4a7a",
    "Psykologi": "#7a4a6a",
    "Religion": "#6b5a2b",
    "Informatik": "#245a6b",
    "Teknologi": "#4a4a6b",
    "Musik": "#7a3a5a",
    "Billedkunst": "#8a4a2b",
    "Design": "#8a4a2b",
    "Idræt": "#7a4426",
}


@dataclass(frozen=True)
class Sheet:
    subject: str
    name: str
    html: Path
    pdf: Path


# --------------------------------------------------------------------------- #
# Colour helpers
# --------------------------------------------------------------------------- #

def _hsl_hex(hue: float, sat: float, light: float) -> str:
    """HSL (0..1 hue) to #rrggbb."""
    def channel(n: int) -> float:
        k = (n + hue * 12) % 12
        a = sat * min(light, 1 - light)
        return light - a * max(-1.0, min(k - 3.0, 9.0 - k, 1.0))

    return "#%02x%02x%02x" % tuple(round(channel(n) * 255) for n in (0, 8, 4))


def _shade(colour: str, amount: float) -> str:
    """Darken (amount < 0) or lighten (amount > 0) a #rrggbb colour."""
    r, g, b = (int(colour[i:i + 2], 16) for i in (1, 3, 5))
    if amount >= 0:
        r, g, b = (round(c + (255 - c) * amount) for c in (r, g, b))
    else:
        r, g, b = (round(c * (1 + amount)) for c in (r, g, b))
    return "#%02x%02x%02x" % (r, g, b)


def accents(subject: str) -> tuple[str, str]:
    base = ACCENTS.get(subject)
    if base is None:
        digest = hashlib.md5(subject.encode("utf-8")).hexdigest()
        base = _hsl_hex((int(digest[:6], 16) % 360) / 360, 0.52, 0.34)
    return base, _shade(base, -0.12)


# --------------------------------------------------------------------------- #
# Discovery
# --------------------------------------------------------------------------- #

def subject_dirs() -> list[Path]:
    if not CONTENT_DIR.is_dir():
        return []
    return sorted(
        p for p in CONTENT_DIR.iterdir()
        if p.is_dir() and p.name not in SKIP_DIRS and not p.name.startswith((".", "_"))
    )


def find_sheets(targets: list[str]) -> list[Sheet]:
    paths: list[Path] = []
    if not targets:
        for folder in subject_dirs():
            paths += sorted(folder.glob("*.html"))
    else:
        for target in targets:
            path = Path(target)
            if not path.is_absolute():
                # accept "Matematik", "content/Matematik" and "Matematik/Sheet.html"
                for candidate in (ROOT / path, CONTENT_DIR / path):
                    if candidate.exists():
                        path = candidate
                        break
                else:
                    path = ROOT / path
            if path.is_dir():
                paths += sorted(path.glob("*.html"))
            elif path.suffix.lower() in (".html", ".htm"):
                if not path.exists():
                    sys.exit(f"No such sheet: {target}")
                paths.append(path)
            else:
                sys.exit(f"Not an HTML file or a folder: {target}")

    sheets: dict[Path, Sheet] = {}
    for html in paths:
        html = html.resolve()
        try:
            rel = html.relative_to(CONTENT_DIR.resolve())
        except ValueError:
            sys.exit(f"{html} is outside content/ - a sheet must live at content/Subject/Name.html")
        if len(rel.parts) != 2:
            sys.exit(f"content/{rel.as_posix()}: a sheet must sit directly in a subject folder")
        name = html.stem.replace("_", " ")
        pdf = DIST_DIR / rel.parts[0] / (html.stem + ".pdf")
        sheets[html] = Sheet(rel.parts[0], name, html, pdf)
    return sorted(sheets.values(), key=lambda s: (s.subject.lower(), s.name.lower()))


def up_to_date(sheet: Sheet) -> bool:
    if not sheet.pdf.exists():
        return False
    newest_source = max(sheet.html.stat().st_mtime, CSS_FILE.stat().st_mtime, BUILD_FILE.stat().st_mtime)
    return sheet.pdf.stat().st_mtime >= newest_source


# --------------------------------------------------------------------------- #
# HTML preparation
# --------------------------------------------------------------------------- #

def read_html(path: Path) -> str:
    data = path.read_bytes()
    for encoding in ("utf-8-sig", "utf-8", "cp1252"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", "replace")


def compose(sheet: Sheet) -> str:
    """Return the HTML Chromium actually prints."""
    raw = read_html(sheet.html)
    accent, accent_ink = accents(sheet.subject)
    css = CSS_FILE.read_text(encoding="utf-8")
    head = (
        '<meta charset="utf-8">\n'
        '<meta http-equiv="Content-Security-Policy" content="script-src \'none\'">\n'
        f'<style id="factsheet-style">\n:root{{--accent:{accent};--accent-ink:{accent_ink};}}\n{css}\n</style>'
    )
    if not re.search(r"<title\b", raw, re.I):
        head += f"\n<title>{escape(sheet.name)}</title>"

    if not re.search(r"<html\b", raw, re.I):
        return (
            '<!doctype html>\n<html lang="da">\n<head>\n'
            f"{head}\n</head>\n<body>\n{raw}\n</body>\n</html>"
        )

    if not re.search(r"<html[^>]*\blang\s*=", raw, re.I):
        raw = re.sub(r"<html\b", '<html lang="da"', raw, count=1, flags=re.I)

    if re.search(r"</head\s*>", raw, re.I):
        raw = re.sub(r"</head\s*>", lambda _m: head + "\n</head>", raw, count=1, flags=re.I)
    elif re.search(r"<body\b", raw, re.I):
        raw = re.sub(r"<body\b", lambda _m: f"<head>\n{head}\n</head>\n<body", raw, count=1, flags=re.I)
    else:
        raw = re.sub(
            r"(<html[^>]*>)",
            lambda m: f"{m.group(1)}\n<head>\n{head}\n</head>",
            raw,
            count=1,
            flags=re.I,
        )
    return raw


def lint(sheet: Sheet, raw: str) -> list[str]:
    problems = []
    if not re.search(r"<h1\b", raw, re.I):
        problems.append("no <h1> title")
    if re.search(r"<script\b", raw, re.I):
        problems.append("<script> is ignored (disabled in the PDF)")
    if re.search(r"<link[^>]+stylesheet", raw, re.I) or re.search(r"<style\b", raw, re.I):
        problems.append("own CSS overrides the house style - move rules to factsheet.css")
    if re.search(r"<img\b", raw, re.I):
        problems.append("use inline SVG instead of <img>")
    return problems


# --------------------------------------------------------------------------- #
# Printing
# --------------------------------------------------------------------------- #

def footer(label: str, numbers: bool) -> str:
    counter = ('<span class="pageNumber"></span> / <span class="totalPages"></span>') if numbers else ""
    return (
        '<div style="width:100%; padding:0 8mm 3mm; font-family:\'Segoe UI\',Arial,sans-serif; '
        'font-size:8px; color:#5b6470;">'
        '<div style="border-top:0.5px solid #ccd2d8; padding-top:1.2mm; display:flex; '
        'justify-content:space-between; gap:6mm; align-items:flex-end;">'
        f'<span style="letter-spacing:0.5px; text-transform:uppercase;">{escape(label)}</span>'
        f'<span style="white-space:nowrap;">{counter}</span>'
        "</div></div>"
    )


def page_count(pdf_bytes: bytes) -> int:
    from pypdf import PdfReader

    return len(PdfReader(io.BytesIO(pdf_bytes)).pages)


def build(sheets: list[Sheet], force: bool, quiet: bool) -> int:
    from playwright.sync_api import sync_playwright

    built = 0
    failures = 0
    total_pages = 0
    skipped = 0
    started = time.time()

    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page()
        page.emulate_media(media="print")

        for sheet in sheets:
            rel_html = sheet.html.relative_to(ROOT).as_posix()
            rel_pdf = sheet.pdf.relative_to(ROOT).as_posix()

            if not force and up_to_date(sheet):
                skipped += 1
                if not quiet:
                    print(f"  up   {rel_pdf}")
                continue

            problems = lint(sheet, read_html(sheet.html))
            label = f"{sheet.subject.upper()} · {sheet.name}"
            t0 = time.perf_counter()
            try:
                page.set_content(compose(sheet), wait_until="load")
                pdf = page.pdf(
                    display_header_footer=True,
                    header_template="<div></div>",
                    footer_template=footer(label, False),
                    **PRINT,
                )
                pages = page_count(pdf)
                if pages > 1:
                    pdf = page.pdf(
                        display_header_footer=True,
                        header_template="<div></div>",
                        footer_template=footer(label, True),
                        **PRINT,
                    )
                    pages = page_count(pdf)

                sheet.pdf.parent.mkdir(parents=True, exist_ok=True)
                tmp = sheet.pdf.with_name(sheet.pdf.name + ".tmp")
                tmp.write_bytes(pdf)
                os.replace(tmp, sheet.pdf)
            except Exception as exc:  # noqa: BLE001 - report and continue
                failures += 1
                print(f"  FAIL {rel_html}: {type(exc).__name__}: {exc}")
                continue

            built += 1
            total_pages += pages
            if not quiet:
                print(f"  ok   {rel_pdf}  {pages} p  {time.perf_counter() - t0:.2f}s")
                for problem in problems:
                    print(f"       ! {problem}")
                if pages > MAX_PAGES:
                    print(f"       ! {pages} pages - target is 1-{MAX_PAGES}; cut, do not shrink the type")

        browser.close()

    if not quiet:
        elapsed = time.time() - started
        print(
            f"\n{built} PDF(s) rebuilt, {skipped} up to date, {total_pages} page(s) total, "
            f"{elapsed:.1f}s"
        )
        if failures:
            print(f"{failures} sheet(s) FAILED")
    return 1 if failures else 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Render content HTML in the subject folders to A4 PDFs.",
        epilog="Examples: python build.py | python build.py Matematik | python build.py --force",
    )
    parser.add_argument("targets", nargs="*", help="subject folder(s) or .html file(s); default: all")
    parser.add_argument("-f", "--force", action="store_true", help="rebuild even when the PDF is up to date")
    parser.add_argument("-q", "--quiet", action="store_true", help="only print failures")
    args = parser.parse_args(argv)

    if not CSS_FILE.exists():
        sys.exit(f"Missing stylesheet: {CSS_FILE}")

    try:
        import playwright  # noqa: F401
        import pypdf  # noqa: F401
    except ImportError:
        sys.exit(
            "Missing dependencies. Run:\n"
            "  pip install -r requirements.txt\n"
            "  python -m playwright install chromium"
        )

    sheets = find_sheets(args.targets)
    if not sheets:
        print("No sheets found. Add content/Subject/Name.html first.")
        return 0

    try:
        return build(sheets, args.force, args.quiet)
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
