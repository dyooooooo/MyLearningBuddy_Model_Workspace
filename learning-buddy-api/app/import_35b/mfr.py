"""Optional Math-Formula-Recognition (MFR) side model integration.

This is a *pluggable* enhancement, not a skeleton change: the 35B blueprint
pipeline works unchanged when ``settings.mfr_enabled`` is False (the default).
When enabled, each ``equation`` block is re-cropped from the source PDF at
native resolution and sent to an external MFR service (Pix2Text / UniMERNet
style) that returns LaTeX.  A successful result replaces the equation's LaTeX;
any failure leaves the block exactly as the 35B model produced it.

Enable via env (pydantic-settings field names, no prefix):
    MFR_ENABLED=true
    MFR_URL=http://mfr:8503/mfr
The endpoint must accept a multipart ``image`` upload and return JSON with a
``latex`` (or ``text``) field.
"""
from __future__ import annotations

import logging
import re

from app.config import settings
from app.import_35b.asset_crop import _norm_bbox_to_pdf_rect
from app.import_35b.schemas import EquationPayload, PageBlueprint
from app.math.normalize import _call_bridge

logger = logging.getLogger(__name__)

# A single formula should never need this much LaTeX; longer output is almost
# always a pix2text degeneration (repeated fragments) rather than a real result.
_MFR_MAX_LEN = 400
# Repeated ``\frac{i}{2}\frac{i}{2}...`` or ``\bar x \bar x ...`` loops are the
# classic MFR failure mode; a chunk repeated many times is also degenerate.
_MFR_LOOP_RES = (
    re.compile(r"(\\frac\{[^}]{1,3}\}\{[^}]{1,3}\}\s*){5,}"),
    re.compile(r"(\\(?:bar|tilde|hat|mathrm|jmath|imath)\b[^ ]{0,6}\s*){6,}"),
    re.compile(r"(.{6,20}?)\1{7,}"),
)


def _mfr_output_sane(latex: str) -> bool:
    """Gate MFR output: sane length, no degenerate loops, MathJax-renderable.

    MFR (pix2text) is only trusted when its result clears all three checks;
    otherwise the block keeps whatever the 35B model produced.
    """
    latex = (latex or "").strip()
    if not latex or len(latex) > _MFR_MAX_LEN:
        return False
    if any(rx.search(latex) for rx in _MFR_LOOP_RES):
        return False
    return bool(_call_bridge(latex, "latex").get("valid"))


def _looks_like_single_formula(bbox: list[float]) -> bool:
    """Only crop bboxes that plausibly frame one standalone formula.

    bbox is normalized to 0..1000.  A tall or large region is a multi-line text
    block, not an equation, so we must not send it to MFR (that is how whole
    paragraphs got turned into garbage LaTeX).
    """
    x0, y0, x1, y1 = bbox
    w, h = x1 - x0, y1 - y0
    if w <= 0 or h <= 0:
        return False
    if h > 120:          # >12% of page height ≈ several text lines
        return False
    if w * h > 200_000:  # e.g. > half-width × 40% height ≈ a block, not a formula
        return False
    return True


def _model_latex_ok(latex: str) -> bool:
    return bool(latex) and bool(_call_bridge(latex, "latex").get("valid"))


class MFRClient:
    """Thin HTTP client for an external MFR service.

    The client never raises to callers: ``recognize`` returns ``None`` on any
    transport/parse error so the pipeline can fall back to the existing LaTeX.
    """

    def __init__(
        self,
        *,
        url: str | None = None,
        timeout_s: float | None = None,
        backend: str | None = None,
    ) -> None:
        self.url = (url if url is not None else settings.mfr_url).strip()
        self.timeout_s = timeout_s if timeout_s is not None else settings.mfr_timeout_s
        self.backend = backend or settings.mfr_backend

    @property
    def available(self) -> bool:
        return bool(self.url)

    def recognize(self, image_bytes: bytes) -> str | None:
        if not self.available or not image_bytes:
            return None
        try:
            import httpx

            files = {"image": ("equation.png", image_bytes, "image/png")}
            data = {"backend": self.backend}
            resp = httpx.post(self.url, files=files, data=data, timeout=self.timeout_s)
            resp.raise_for_status()
            payload = resp.json()
        except Exception as exc:  # noqa: BLE001 — degrade gracefully
            logger.warning("MFR request failed (%s): %s", self.url, exc)
            return None

        latex = payload.get("latex") or payload.get("text") if isinstance(payload, dict) else None
        latex = (latex or "").strip()
        if len(latex) < settings.mfr_min_latex_len:
            return None
        return latex


def _crop_equation_png(page, bbox: list[float], page_w: float, page_h: float, dpi: int) -> bytes:
    clip = _norm_bbox_to_pdf_rect(bbox, page_w, page_h)
    pix = page.get_pixmap(dpi=dpi, alpha=False, clip=clip)
    return pix.tobytes("png")


def enrich_equations_with_mfr(
    *,
    pdf_bytes: bytes,
    blueprint: PageBlueprint,
    dpi: int | None = None,
    client: MFRClient | None = None,
) -> PageBlueprint:
    """Fill equation LaTeX from an external MFR model when enabled.

    No-op (returns the blueprint unchanged) unless ``settings.mfr_enabled`` is
    True and an MFR URL is configured — keeping the default skeleton intact.
    """
    if not settings.mfr_enabled:
        return blueprint
    client = client or MFRClient()
    if not client.available or not pdf_bytes:
        return blueprint
    if not any(b.kind == "equation" for b in blueprint.blocks):
        return blueprint

    import fitz

    dpi = dpi or settings.mfr_dpi
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    try:
        page = doc.load_page(blueprint.page_no - 1)
        page_w, page_h = float(page.rect.width), float(page.rect.height)
        blocks = []
        for block in blueprint.blocks:
            if block.kind != "equation":
                blocks.append(block)
                continue
            # 1) Only touch bboxes that look like a single standalone formula.
            if not _looks_like_single_formula(block.bbox):
                blocks.append(block)
                continue
            # 2) If the 35B model already produced renderable LaTeX, trust it —
            #    never overwrite good LaTeX with an unvalidated MFR guess.
            model_latex = block.content.equation_latex()
            if _model_latex_ok(model_latex):
                blocks.append(block)
                continue
            # 3) Run MFR; accept its output only if it passes the sanity gate.
            try:
                png = _crop_equation_png(page, block.bbox, page_w, page_h, dpi)
                latex = client.recognize(png)
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "MFR crop/recognize failed page=%d block=%s: %s",
                    blueprint.page_no,
                    block.id,
                    exc,
                )
                latex = None
            if not latex or not _mfr_output_sane(latex):
                if latex:
                    logger.info(
                        "MFR output rejected by gate page=%d block=%s len=%d",
                        blueprint.page_no,
                        block.id,
                        len(latex),
                    )
                blocks.append(block)
                continue
            fallback = block.content.equation_fallback_text() or model_latex
            content = block.content.model_copy(
                update={"equation": EquationPayload(latex=latex, fallback_text=fallback or None)}
            )
            blocks.append(block.model_copy(update={"content": content}))
        return blueprint.model_copy(update={"blocks": blocks})
    finally:
        doc.close()
