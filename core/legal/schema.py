"""Metadata schema definitions for Ghana legal documents.

All fields match the 17O-B taxonomy (jurisdiction, court, year, citation,
judge, parties, status) plus legacy YAML fields from 17O-A scraping
pipelines (title, date, type, source_url).
"""

from __future__ import annotations

import enum
from dataclasses import dataclass, field, asdict
from typing import Optional


class DocumentStatus(str, enum.Enum):
    CURRENT = "current"
    OVERRULED = "overruled"
    AMENDED = "amended"


class DocumentType(str, enum.Enum):
    ACT = "act"
    BILL = "bill"
    REGULATION = "regulation"
    JUDGMENT = "judgment"
    RULING = "ruling"
    GAZETTE = "gazette"
    ORDER = "order"
    OTHER = "other"


class Jurisdiction(str, enum.Enum):
    GHANA = "ghana"
    GHANA_SUPREME_COURT = "ghana-supreme-court"
    GHANA_COURT_OF_APPEAL = "ghana-court-of-appeal"
    GHANA_HIGH_COURT = "ghana-high-court"
    GHANA_PARLIAMENT = "ghana-parliament"


@dataclass
class LegalDocument:
    """Single legal document's metadata row."""
    jurisdiction: str
    court: str
    year: int
    citation: str
    judge: str = ""
    parties: str = ""
    status: str = DocumentStatus.CURRENT.value
    title: str = ""
    date: str = ""
    type: str = ""
    source_url: str = ""
    id: Optional[int] = None
    created_at: str = ""
    updated_at: str = ""

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_row(cls, row: tuple) -> "LegalDocument":
        return cls(
            id=row[0], jurisdiction=row[1], court=row[2], year=row[3],
            citation=row[4], judge=row[5] or "", parties=row[6] or "",
            status=row[7], title=row[8] or "", date=row[9] or "",
            type=row[10] or "", source_url=row[11] or "",
            created_at=row[12] or "", updated_at=row[13] or "",
        )


_REQUIRED_FIELDS = ("jurisdiction", "court", "year", "citation")


def validate_document(doc: LegalDocument) -> list[str]:
    errors: list[str] = []
    for field in _REQUIRED_FIELDS:
        value = getattr(doc, field, None)
        if value is None or (isinstance(value, str) and not value.strip()):
            errors.append(f"Missing required field: {field}")
        elif field == "year":
            try:
                int(value)
            except (TypeError, ValueError):
                errors.append(f"year must be an integer, got: {value!r}")
    if doc.status not in {s.value for s in DocumentStatus}:
        errors.append(f"Invalid status '{doc.status}'.")
    if doc.jurisdiction not in {j.value for j in Jurisdiction}:
        errors.append(f"Invalid jurisdiction '{doc.jurisdiction}'.")
    return errors
