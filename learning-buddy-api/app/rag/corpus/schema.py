from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

NodeType = Literal[
    "kp",
    "textbook",
    "section",
    "chunk",
    "method",
    "shortcut",
    "question",
    "tag",
    "media",
]

DocType = Literal[
    "syllabus",
    "question_bank",
    "keyword",
    "textbook",
    "section",
    "chunk",
    "method",
    "shortcut",
    "tag",
]

# Bidirectional edge pairs (rel_type forward / inverse)
EDGE_PAIRS: dict[str, str] = {
    "covers_kp": "examined_by",
    "examined_by": "covers_kp",
    "in_textbook": "contains_section",
    "contains_section": "in_textbook",
    "contains_chunk": "in_section",
    "in_section": "contains_chunk",
    "defines_kp": "defined_in",
    "defined_in": "defines_kp",
    "has_standard_method": "applies_to_kp",
    "applies_to_kp": "has_standard_method",
    "has_shortcut": "derived_from_kp",
    "derived_from_kp": "has_shortcut",
    "uses_shortcut": "enabled_by_method",
    "enabled_by_method": "uses_shortcut",
    "applies_method": "method_for",
    "method_for": "applies_method",
    "uses_shortcut_q": "shortcut_for",
    "shortcut_for": "uses_shortcut_q",
    "derived_from_chunk": "derives_shortcut",
    "derives_shortcut": "derived_from_chunk",
    "illustrates_method": "grounded_in_chunk",
    "grounded_in_chunk": "illustrates_method",
    "tagged_with": "tags_node",
    "tags_node": "tagged_with",
    "scoped_in": "covers_kp_tb",
    "covers_kp_tb": "scoped_in",
    "teaches_method": "in_textbook_m",
    "in_textbook_m": "teaches_method",
    "introduces_shortcut": "from_textbook",
    "from_textbook": "introduces_shortcut",
    "section_methods": "taught_in_section",
    "taught_in_section": "section_methods",
    "section_shortcuts": "introduced_in_section",
    "introduced_in_section": "section_shortcuts",
    "section_questions": "in_section_q",
    "in_section_q": "section_questions",
    "example_for": "references_chunk",
    "references_chunk": "example_for",
    "prerequisite": "required_by",
    "required_by": "prerequisite",
    "same_type": "same_type",
    "similar_method": "similar_method",
    "same_family": "same_family",
    "related_chunk": "related_chunk",
    "follows": "preceded_by",
    "preceded_by": "follows",
    "successor": "predecessor",
    "predecessor": "successor",
}


class NodeLinks(BaseModel):
    primary_kp: str | None = None
    secondary_kp: list[str] = Field(default_factory=list)
    textbook_id: str | None = None
    section_id: str | None = None
    chunk_ids: list[str] = Field(default_factory=list)
    method_ids: list[str] = Field(default_factory=list)
    shortcut_ids: list[str] = Field(default_factory=list)
    tag_ids: list[str] = Field(default_factory=list)
    source_ref: str | None = None


class CorpusDocument(BaseModel):
    doc_id: str
    doc_type: DocType
    subject: str
    grade_band: str
    kp_id: str
    text: str
    label: str = ""
    parent_id: str = ""
    summary: str = ""
    full_text: str = ""
    node_type: NodeType | None = None
    tags: list[str] = Field(default_factory=list)
    links: dict[str, Any] = Field(default_factory=dict)
    meta: dict[str, Any] = Field(default_factory=dict)

    @property
    def node_id(self) -> str:
        return self.doc_id

    @property
    def embed_text(self) -> str:
        return (self.summary or self.text or self.full_text).strip()
