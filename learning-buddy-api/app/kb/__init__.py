"""Knowledge-base line — isolated from the textbook-studio import stack."""

from app.kb.hint import pick_textbook_hint
from app.kb.import_service import process_kb_book

__all__ = ["pick_textbook_hint", "process_kb_book"]
