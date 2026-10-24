#!/usr/bin/env python3
"""Static, dependency-free checks for the Papertrail deliverable.

Checks structure, local asset paths, ARIA references, control semantics,
absence of external resources, CSS breakpoints, and key contrast pairs.
It does not render the page; rendered layout is listed as unverified.
"""
from __future__ import annotations

import html
import pathlib
import re
import sys
from html.parser import HTMLParser

ROOT = pathlib.Path(__file__).resolve().parent.parent
INDEX = ROOT / "index.html"
CSS = ROOT / "css" / "style.css"
JS = [ROOT / "js" / "data.js", ROOT / "js" / "core.js", ROOT / "js" / "app.js"]

errors: list[str] = []
notes: list[str] = []


class Collector(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.ids: list[str] = []
        self.labels_for: list[str] = []
        self.aria_refs: list[tuple[str, str]] = []
        self.buttons_without_type: list[str] = []
        self.inputs: list[dict[str, str]] = []
        self.links: list[str] = []
        self.scripts: list[str] = []
        self.stylesheets: list[str] = []
        self.inline_handlers: list[str] = []
        self.external: list[str] = []
        self.headings: list[tuple[str, str]] = []
        self._heading: str | None = None
        self._heading_text: list[str] = []
        self.has_lang = False
        self.has_viewport = False
        self.title = ""
        self._in_title = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        data = {key: (value or "") for key, value in attrs}
        if tag == "html" and data.get("lang"):
            self.has_lang = True
        if tag == "meta" and data.get("name") == "viewport":
            self.has_viewport = True
        if tag == "title":
            self._in_title = True
        if "id" in data:
            self.ids.append(data["id"])
        if tag == "label" and "for" in data:
            self.labels_for.append(data["for"])
        for key in ("aria-labelledby", "aria-describedby", "aria-controls"):
            if key in data:
                for ref in data[key].split():
                    self.aria_refs.append((key, ref))
        if tag == "button" and "type" not in data:
            self.buttons_without_type.append(data.get("class", "(no class)"))
        if tag == "input":
            self.inputs.append(data)
        if tag == "a" and "href" in data:
            self.links.append(data["href"])
        if tag == "script" and "src" in data:
            self.scripts.append(data["src"])
        if tag == "link" and data.get("rel") == "stylesheet":
            self.stylesheets.append(data.get("href", ""))
        for key, value in data.items():
            if key.startswith("on"):
                self.inline_handlers.append(f"{tag}[{key}]")
            if re.match(r"^(https?:)?//", value):
                self.external.append(value)
        if tag in {"h1", "h2", "h3"}:
            self._heading = tag
            self._heading_text = []

    def handle_endtag(self, tag: str) -> None:
        if tag == "title":
            self._in_title = False
        if self._heading == tag:
            self.headings.append((tag, "".join(self._heading_text).strip()))
            self._heading = None

    def handle_data(self, data: str) -> None:
        if self._in_title:
            self.title += data
        if self._heading:
            self._heading_text.append(data)


def contrast(hex_a: str, hex_b: str) -> float:
    def channel(value: int) -> float:
        value /= 255
        return value / 12.92 if value <= 0.04045 else ((value + 0.055) / 1.055) ** 2.4

    def luminance(hex_color: str) -> float:
        value = hex_color.lstrip("#")
        if len(value) == 3:
            value = "".join(character * 2 for character in value)
        red, green, blue = (int(value[i:i + 2], 16) for i in (0, 2, 4))
        return 0.2126 * channel(red) + 0.7152 * channel(green) + 0.0722 * channel(blue)

    light, dark = sorted((luminance(hex_a), luminance(hex_b)), reverse=True)
    return (light + 0.05) / (dark + 0.05)


def composite(fg: str, bg: str, alpha: float) -> str:
    fg_value = fg.lstrip("#")
    bg_value = bg.lstrip("#")
    out = []
    for index in (0, 2, 4):
        f = int(fg_value[index:index + 2], 16)
        b = int(bg_value[index:index + 2], 16)
        out.append(round(f * alpha + b * (1 - alpha)))
    return "#" + "".join(f"{value:02x}" for value in out)


# 1. Parse and structural checks.
text = INDEX.read_text(encoding="utf-8")
parser = Collector()
parser.feed(text)

if not parser.has_lang:
    errors.append("html lang missing")
if not parser.has_viewport:
    errors.append("viewport meta missing")
if "Papertrail" not in parser.title:
    errors.append("document title missing product name")

duplicates = sorted({item for item in parser.ids if parser.ids.count(item) > 1})
if duplicates:
    errors.append(f"duplicate ids: {duplicates}")

for target in parser.labels_for:
    if target not in parser.ids:
        errors.append(f"label for missing target: {target}")

for attribute, ref in parser.aria_refs:
    if ref not in parser.ids:
        errors.append(f"{attribute} missing target: {ref}")

if parser.buttons_without_type:
    errors.append(f"buttons without type: {parser.buttons_without_type}")

for input_data in parser.inputs:
    if input_data.get("type") not in {"search", "text", "checkbox", "radio"}:
        errors.append(f"unexpected input type: {input_data}")
    if "id" not in input_data:
        errors.append(f"input without id: {input_data}")
    elif input_data["id"] not in parser.labels_for:
        errors.append(f"input without label: {input_data['id']}")

if parser.inline_handlers:
    errors.append(f"inline event handlers: {parser.inline_handlers}")
if parser.external:
    errors.append(f"external URLs in HTML: {parser.external}")

for href in parser.links:
    if href.startswith("#") and href != "#" and href[1:] not in parser.ids:
        errors.append(f"fragment target missing: {href}")

for asset in parser.scripts + parser.stylesheets:
    if not (ROOT / asset).exists():
        errors.append(f"missing local asset: {asset}")

if not any(tag == "h1" for tag, _ in parser.headings):
    errors.append("no h1")
if [tag for tag, _ in parser.headings].count("h1") != 1:
    errors.append("h1 count is not 1")

# 2. CSS checks.
css = CSS.read_text(encoding="utf-8")
if css.count("{") != css.count("}"):
    errors.append("CSS braces unbalanced")
if "min-width: 980px" in css:
    errors.append("legacy fixed minimum width still present")
if "@media (min-width: 1200px)" not in css:
    errors.append("desktop breakpoint missing")
if "@media (min-width: 900px)" not in css:
    errors.append("tablet breakpoint missing")
if "max-width: 559px" not in css:
    errors.append("narrow-screen breakpoint missing")
if re.search(r"url\(\s*['\"]?https?:", css):
    errors.append("external URL in CSS")
if "@media (prefers-reduced-motion: reduce)" not in css:
    errors.append("reduced-motion handling missing")

# 3. JS checks.
for path in JS:
    source = path.read_text(encoding="utf-8")
    if re.search(r"https?://", source):
        errors.append(f"external URL in {path.name}")
if "fetch(" in "\n".join(path.read_text(encoding="utf-8") for path in JS):
    errors.append("fetch() present although the page must stay local")

# 4. Contrast pairs (text only; borders are decorative).
pairs = {
    "ink / paper": ("#1a231d", "#f4f5f0", 4.5),
    "ink-2 / paper": ("#39443c", "#f4f5f0", 4.5),
    "muted / paper": ("#556159", "#f4f5f0", 4.5),
    "muted / surface": ("#556159", "#ffffff", 4.5),
    "ink / surface": ("#1a231d", "#ffffff", 4.5),
    "white / pine": ("#ffffff", "#143b2e", 4.5),
    "pine-ink / pine": ("#f0f4ee", "#143b2e", 4.5),
    "pine-muted / pine": ("#a9bdb1", "#143b2e", 4.5),
    "unread / surface": ("#5d6860", "#ffffff", 4.5),
    "reading / surface": ("#285b9b", "#ffffff", 4.5),
    "read / surface": ("#28684a", "#ffffff", 4.5),
    "white / read": ("#ffffff", "#28684a", 4.5),
    "white / reading": ("#ffffff", "#285b9b", 4.5),
    "white / unread": ("#ffffff", "#5d6860", 4.5),
    "viz / surface": ("#a14a27", "#ffffff", 4.5),
    "hci / surface": ("#2a6872", "#ffffff", 4.5),
    "method / surface": ("#77601f", "#ffffff", 4.5),
    "focus / paper": ("#1a5fd0", "#f4f5f0", 3.0),
    "notice text / notice bg": ("#5c4a1e", "#fbf3e4", 4.5),
}
for name, (foreground, background, minimum) in pairs.items():
    ratio = contrast(foreground, background)
    if ratio < minimum:
        errors.append(f"contrast {name}: {ratio:.2f} < {minimum}")
    else:
        notes.append(f"contrast {name}: {ratio:.2f}")

# Active tab count uses white at 72% over pine.
active_count = composite("#ffffff", "#143b2e", 0.72)
ratio = contrast(active_count, "#143b2e")
if ratio < 4.5:
    errors.append(f"contrast active tab count ({active_count} / pine): {ratio:.2f} < 4.5")
else:
    notes.append(f"contrast active tab count: {ratio:.2f}")

# 5. Required demo truth markers.
for phrase in ("演示数据", "虚构演示资料", "无在线同步", "仅本页操作"):
    if phrase not in text:
        errors.append(f"missing honesty marker: {phrase}")

# 6. Required controls and landmarks exist in source.
for required in (
    'id="search"', 'id="sort"', 'id="status-filter"', 'id="topic-list"',
    'id="paper-list"', 'id="detail-pane"', 'data-status="unread"',
    'data-status="reading"', 'data-status="read"', 'data-topic="all"',
):
    if required not in text:
        errors.append(f"missing required markup: {required}")

print("verify-static: " + ("PASS" if not errors else "FAIL"))
print(f"  ids: {len(parser.ids)}, labels: {len(parser.labels_for)}, aria refs: {len(parser.aria_refs)}")
print(f"  headings: {[tag for tag, _ in parser.headings]}")
print(f"  contrast pairs checked: {len(pairs) + 1}")
if errors:
    for error in errors:
        print("  ERROR:", error)
    sys.exit(1)
for note in notes:
    print("  " + note)
