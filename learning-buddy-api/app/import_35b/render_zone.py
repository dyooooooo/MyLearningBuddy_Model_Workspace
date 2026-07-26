"""Self-contained page skeleton + helpers for the 35B import line.

Copied (intentionally, per the "zero shared code" isolation mandate) from the
former ``app.services.document_ir`` helpers so the 35B pipeline has NO import
dependency on the MinerU rendering module. Changing MinerU's document_ir must
never affect 35B output, and vice versa.
"""
from __future__ import annotations

import re


def escape_html(text: str) -> str:
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def _zone_container_class(zone: str) -> str:
    if zone == "aside_left":
        return "lb-aside lb-aside--left"
    if zone == "aside_right":
        return "lb-aside lb-aside--right"
    if zone == "full_bleed":
        return "lb-full-bleed"
    return "lb-main"


def sanitize_page_html(html: str) -> str:
    try:
        import bleach
    except ImportError:
        return html
    allowed_tags = bleach.sanitizer.ALLOWED_TAGS.union(
        {
            "article",
            "header",
            "section",
            "aside",
            "figure",
            "figcaption",
            "img",
            "table",
            "caption",
            "thead",
            "tbody",
            "tr",
            "th",
            "td",
            "h1",
            "h2",
            "h3",
            "h4",
            "span",
            "div",
            "p",
            "br",
            "ol",
            "ul",
            "li",
        }
    )
    allowed_attrs = {
        **bleach.sanitizer.ALLOWED_ATTRIBUTES,
        "*": [
            "class",
            "data-page",
            "data-profile",
            "data-zone",
            "data-block-id",
            "data-anchor",
            "data-figure-group",
            "data-latex",
            "data-blank",
        ],
        "img": ["src", "alt", "class"],
    }
    return bleach.clean(html, tags=list(allowed_tags), attributes=allowed_attrs, strip=False)


def render_zone_grid_html(
    *,
    page_no: int,
    profile_id: str,
    header: str,
    by_zone: dict[str, list[str]],
) -> str:
    """Single page skeleton for the 35B line (lb-page / lb-grid / zones)."""
    parts = [
        f'<article class="lb-page" data-page="{page_no}" data-profile="{escape_html(profile_id)}">',
    ]
    if header and header.strip():
        parts.append(f'<header class="lb-running-hd">{escape_html(header.strip())}</header>')
    parts.append('<div class="lb-grid">')

    for zone in ("aside_left", "main", "aside_right"):
        chunks = by_zone.get(zone) or []
        cls = _zone_container_class(zone)
        tag = "aside" if zone.startswith("aside") else "section"
        inner = "".join(chunks)
        if not inner and zone != "main":
            inner = '<div class="lb-col-placeholder" aria-hidden="true"></div>'
        parts.append(f'<{tag} class="{cls}" data-zone="{zone}">{inner}</{tag}>')

    parts.append("</div>")

    full_bleed = by_zone.get("full_bleed") or []
    if full_bleed:
        parts.append(
            f'<div class="lb-full-bleed" data-zone="full_bleed">{"".join(full_bleed)}</div>'
        )

    parts.append("</article>")
    return sanitize_page_html("\n".join(parts))


def html_to_plain_text(html: str) -> str:
    try:
        from html.parser import HTMLParser
    except ImportError:
        return re.sub(r"<[^>]+>", " ", html)

    class _Strip(HTMLParser):
        def __init__(self) -> None:
            super().__init__()
            self.parts: list[str] = []

        def handle_data(self, data: str) -> None:
            if data.strip():
                self.parts.append(data.strip())

    parser = _Strip()
    try:
        parser.feed(html)
        parser.close()
    except Exception:
        return re.sub(r"<[^>]+>", " ", html)
    return "\n".join(parser.parts)
