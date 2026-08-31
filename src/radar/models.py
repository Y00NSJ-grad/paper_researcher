from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any


def utc_now() -> datetime:
    return datetime.now(UTC)


@dataclass(slots=True)
class PaperCandidate:
    source: str
    source_id: str
    title: str
    url: str
    abstract: str | None = None
    authors: list[str] = field(default_factory=list)
    affiliations: list[str] = field(default_factory=list)
    published_at: datetime | None = None
    updated_at: datetime | None = None
    venue: str | None = None
    doi: str | None = None
    arxiv_id: str | None = None
    pdf_url: str | None = None
    citation_count: int | None = None
    code_url: str | None = None
    external_ids: dict[str, str] = field(default_factory=dict)
    query_ids: list[str] = field(default_factory=list)
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class ScoredPaper:
    candidate: PaperCandidate
    score: float
    tags: dict[str, list[str]]
    reasons: list[str]


@dataclass(slots=True)
class PaperSummary:
    paper: str
    problem: str
    method: str
    benchmark: str
    why_it_matters: str
    can_i_use_it: str

    def as_lines(self) -> list[str]:
        return [
            f"Paper: {self.paper}",
            f"Problem: {self.problem}",
            f"Method: {self.method}",
            f"Benchmark: {self.benchmark}",
            f"Why it matters: {self.why_it_matters}",
            f"Can I use it?: {self.can_i_use_it}",
        ]


@dataclass(slots=True)
class TrendEvidence:
    claim: str
    paper_ids: list[str] = field(default_factory=list)


@dataclass(slots=True)
class TrendSection:
    overview: str
    key_trends: list[str] = field(default_factory=list)
    evidence: list[TrendEvidence] = field(default_factory=list)
    research_opportunities: list[str] = field(default_factory=list)
    limitations: list[str] = field(default_factory=list)


@dataclass(slots=True)
class MonthlyTrendAnalysis:
    executive_summary: str
    physical_ai: TrendSection
    quantum_ai: TrendSection
    domains: TrendSection
