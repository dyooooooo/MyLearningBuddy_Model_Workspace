from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass

from app.rag.corpus.schema import CorpusDocument


@dataclass
class VectorHit:
    doc_id: str
    kp_id: str
    score: float
    text: str
    label: str
    doc_type: str
    source: str


class VectorStore(ABC):
    @abstractmethod
    def upsert_documents(self, docs: list[CorpusDocument], vectors: list[list[float]]) -> None:
        raise NotImplementedError

    @abstractmethod
    def search(
        self,
        query_vector: list[float],
        *,
        subject: str,
        grade_band: str,
        top_k: int = 5,
    ) -> list[VectorHit]:
        raise NotImplementedError

    @abstractmethod
    def count(self) -> int:
        raise NotImplementedError

    @abstractmethod
    def recreate_collection(self) -> None:
        raise NotImplementedError

    @abstractmethod
    def health_ok(self) -> bool:
        raise NotImplementedError
