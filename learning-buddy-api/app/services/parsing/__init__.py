"""Document-parsing bridge: Adapter + ParsedDocument IR.

Public surface:
    ParsedDocument  — engine-agnostic parse result
    ParserError     — raised by adapters and ParseService
    get_parse_service — process-wide ParseService singleton
"""
from .parse_service import get_parse_service
from .types import ParsedDocument, ParserError

__all__ = ["ParsedDocument", "ParserError", "get_parse_service"]
