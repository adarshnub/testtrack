from abc import ABC, abstractmethod
from dataclasses import dataclass

from schemas import Bounds, Extraction, Issue, Page


@dataclass(frozen=True)
class Word:
    x0: float
    y0: float
    x1: float
    y1: float
    text: str


@dataclass
class Line:
    page: int
    words: list[Word]
    bounds: Bounds

    @property
    def text(self) -> str:
        return " ".join(word.text for word in self.words)


@dataclass
class Document:
    pages: list[Page]
    lines: list[Line]
    issues: list[Issue]


class BankParser(ABC):
    profile_id: str
    profile_name: str
    version: int = 1

    @abstractmethod
    def supports(self, document: Document) -> bool:
        """Return whether the statement matches this profile's evidence."""

    @abstractmethod
    def extract(self, document: Document) -> Extraction:
        """Normalize the document and preserve the page and row evidence."""
