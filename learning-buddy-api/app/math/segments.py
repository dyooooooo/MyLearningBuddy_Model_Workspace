from __future__ import annotations

from typing import Any

from app.schemas.common import MathSegment, RichText
from app.util.json_util import loads


def rich_to_json(rich: RichText) -> str:
    import json

    return json.dumps(rich.model_dump(), ensure_ascii=False)


def segments_to_rich(raw: str, segments: list[MathSegment]) -> RichText:
    return RichText(raw=raw, segments=segments)


def load_rich_text(raw: str, segments_json: str | list | None) -> RichText | None:
    if not segments_json:
        return None
    data = segments_json if isinstance(segments_json, list) else loads(segments_json, [])
    if not isinstance(data, list) or not data:
        return None
    segments: list[MathSegment] = []
    for item in data:
        if not isinstance(item, dict):
            continue
        segments.append(MathSegment.model_validate(item))
    if not segments:
        return None
    return RichText(raw=raw or "", segments=segments)


def rich_from_dict(data: dict[str, Any] | None) -> RichText | None:
    if not data or not isinstance(data, dict):
        return None
    try:
        return RichText.model_validate(data)
    except Exception:
        return None


def segments_json_dumps(rich: RichText) -> str:
    import json

    return json.dumps([s.model_dump() for s in rich.segments], ensure_ascii=False)


def apply_rich_fields(
    *,
    print_text: str,
    handwriting: str,
    print_segments_json: str = "[]",
    handwriting_segments_json: str = "[]",
) -> tuple[str, str, RichText, RichText]:
    from app.math.normalize import normalize_plain

    print_rich = load_rich_text(print_text, print_segments_json)
    hw_rich = load_rich_text(handwriting, handwriting_segments_json)
    if print_rich is None:
        print_rich = normalize_plain(print_text)
    if hw_rich is None:
        hw_rich = normalize_plain(handwriting)
    return print_rich.raw, segments_json_dumps(print_rich), print_rich, hw_rich
