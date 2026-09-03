from __future__ import annotations

import json
from collections import Counter
from typing import Literal

from openai import OpenAI
from pydantic import BaseModel, Field

from radar.models import (
    MonthlyTrendAnalysis,
    PaperSummary,
    TrendEvidence,
    TrendSection,
    WeeklyInsight,
    WeeklyPaperPick,
    WeeklyTrendAnalysis,
)


class SummarySchema(BaseModel):
    paper: str = Field(description="Paper title")
    problem: str
    method: str
    benchmark: str
    why_it_matters: str
    can_i_use_it: str


class TrendEvidenceSchema(BaseModel):
    claim: str = Field(description="A grounded observation supported by the supplied papers")
    paper_ids: list[str] = Field(description="Supporting IDs such as P12")


class TrendSectionSchema(BaseModel):
    overview: str
    key_trends: list[str]
    evidence: list[TrendEvidenceSchema]
    research_opportunities: list[str]
    limitations: list[str]


class MonthlyTrendSchema(BaseModel):
    executive_summary: str
    physical_ai: TrendSectionSchema
    quantum_ai: TrendSectionSchema
    domains: TrendSectionSchema


class WeeklyInsightSchema(BaseModel):
    title: str
    insight: str
    confidence: Literal["Strong", "Moderate", "Weak", "Insufficient data"]
    paper_ids: list[str] = Field(description="Supporting supplied paper IDs")


class WeeklyPaperPickSchema(BaseModel):
    paper_id: str = Field(description="One supplied current-window paper ID")
    role: str = Field(description="Why category, such as novel idea or useful benchmark")
    why: str


class WeeklyTrendSchema(BaseModel):
    research_pulse: str
    emerging_signals: list[WeeklyInsightSchema]
    cross_domain_convergence: list[WeeklyInsightSchema]
    papers_worth_reading: list[WeeklyPaperPickSchema]
    research_opportunities: list[WeeklyInsightSchema]
    watchlist: list[WeeklyInsightSchema]
    data_coverage: str


SYSTEM_PROMPT = """You summarize research papers for a researcher interested in MARL, MEC,
SAGIN, NTN, UAVs, ISCC, URLLC, satellite routing and handover, digital twins,
physical AI, VLA, diffusion models, quantum AI, edge caching, and graph neural networks.

Use only the supplied title and abstract. Never invent experimental results, datasets,
benchmarks, code availability, or publication status. If a requested fact is absent, write
exactly 'Not stated in abstract'. Keep each field concise and concrete. For can_i_use_it,
identify at most one plausible connection to the researcher's domains and label it as a
hypothesis when the abstract does not validate that application.
"""

TREND_SYSTEM_PROMPT = """You are a research trend analyst. Analyze only the supplied
paper metadata from the requested time window and write every narrative field in Korean.

Produce three independent sections:
1. Physical AI: embodied/physical AI, VLA, world models, diffusion or generative control,
   robotics, and interaction with the physical world.
2. Quantum AI: quantum machine learning, quantum reinforcement learning, quantum
   optimization, and hybrid quantum-classical methods.
3. Domains: application-domain movements such as SAGIN/NTN/satellite, UAV/aerial edge,
   MEC/edge intelligence, ISAC/ISCC/URLLC, digital twins, routing, handover, resource
   allocation, and related areas represented in the data.

Distinguish an observed trend from a single-paper signal. Never invent papers, results,
growth rates, comparisons with earlier months, or causal claims. Cite evidence only with
the supplied paper IDs. If a section has too little evidence, say so explicitly and turn
speculation into clearly labeled research opportunities. Keep the report concrete and
useful for deciding what to read or investigate next.
"""

WEEKLY_TREND_SYSTEM_PROMPT = """You are a weekly research intelligence analyst. Write
every narrative field in Korean and use only the supplied current-window papers, baseline
papers, precomputed counts, and collection coverage.

This is a change-detection report, not a miniature monthly survey. Produce:
- a concise weekly research pulse;
- up to 5 emerging or weakening signals versus the prior 28-day weekly average;
- up to 5 new or repeated cross-domain/method/task combinations;
- up to 5 current papers worth reading, each assigned a useful role;
- up to 5 concrete research opportunities, explicitly labeled as hypotheses when not
  directly validated by papers;
- up to 5 items to watch next week;
- an honest data-coverage assessment.

Confidence means: Strong = at least 3 supporting papers; Moderate = at least 2 papers;
Weak = one-paper signal; Insufficient data = no sound conclusion.
Never invent growth, papers, experiments, source health, or comparisons. Do not call a
single-paper observation a trend. Cite only supplied paper IDs. Paper recommendations
must use current-window IDs. If Physical AI or Quantum AI has no meaningful weekly signal,
say so in the watchlist or coverage instead of manufacturing a section.
"""

MAX_TREND_PAPERS = 100
MAX_ABSTRACT_CHARS = 1200
MAX_WEEKLY_CURRENT_PAPERS = 60
MAX_WEEKLY_BASELINE_PAPERS = 100
MAX_WEEKLY_BASELINE_ABSTRACT_CHARS = 400


class OpenAISummarizer:
    def __init__(self, api_key: str, model: str):
        self.client = OpenAI(api_key=api_key)
        self.model = model

    def summarize(self, title: str, abstract: str) -> PaperSummary:
        response = self.client.responses.parse(
            model=self.model,
            input=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": f"Title:\n{title}\n\nAbstract:\n{abstract}",
                },
            ],
            text_format=SummarySchema,
        )
        parsed = response.output_parsed
        if parsed is None:
            raise RuntimeError("The model returned no structured summary")
        return PaperSummary(
            paper=parsed.paper,
            problem=parsed.problem,
            method=parsed.method,
            benchmark=parsed.benchmark,
            why_it_matters=parsed.why_it_matters,
            can_i_use_it=parsed.can_i_use_it,
        )


def _trend_section(section: TrendSectionSchema) -> TrendSection:
    return TrendSection(
        overview=section.overview,
        key_trends=section.key_trends,
        evidence=[
            TrendEvidence(claim=item.claim, paper_ids=item.paper_ids) for item in section.evidence
        ],
        research_opportunities=section.research_opportunities,
        limitations=section.limitations,
    )


class OpenAITrendAnalyzer:
    def __init__(self, api_key: str, model: str):
        self.client = OpenAI(api_key=api_key)
        self.model = model

    def analyze(self, rows: list[dict], days: int) -> MonthlyTrendAnalysis:
        papers = []
        for row in rows[:MAX_TREND_PAPERS]:
            papers.append(
                {
                    "id": f"P{row['id']}",
                    "title": row["title"],
                    "abstract": (row.get("abstract") or "")[:MAX_ABSTRACT_CHARS],
                    "venue": row.get("venue"),
                    "published_at": row.get("published_at"),
                    "first_seen_at": row.get("first_seen_at"),
                    "tags": _tags(row),
                    "score": row.get("score"),
                }
            )

        response = self.client.responses.parse(
            model=self.model,
            input=[
                {"role": "system", "content": TREND_SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": (
                        f"분석 기간: 최근 {days}일\n"
                        f"고유 논문 수: {len(rows)}\n"
                        "다음 JSON 데이터만 근거로 분석하세요:\n"
                        + json.dumps(papers, ensure_ascii=False, default=str)
                    ),
                },
            ],
            text_format=MonthlyTrendSchema,
        )
        parsed = response.output_parsed
        if parsed is None:
            raise RuntimeError("The model returned no structured trend analysis")
        return MonthlyTrendAnalysis(
            executive_summary=parsed.executive_summary,
            physical_ai=_trend_section(parsed.physical_ai),
            quantum_ai=_trend_section(parsed.quantum_ai),
            domains=_trend_section(parsed.domains),
        )


def _tags(row: dict) -> dict[str, list[str]]:
    try:
        value = json.loads(row.get("tags_json") or "{}")
    except json.JSONDecodeError:
        return {}
    return value if isinstance(value, dict) else {}


def _paper_record(row: dict, abstract_chars: int) -> dict:
    return {
        "id": f"P{row['id']}",
        "title": row["title"],
        "abstract": (row.get("abstract") or "")[:abstract_chars],
        "venue": row.get("venue"),
        "published_at": row.get("published_at"),
        "first_seen_at": row.get("first_seen_at"),
        "tags": _tags(row),
        "score": row.get("score"),
        "has_code": bool(row.get("code_url")),
    }


def _signal_counts(rows: list[dict]) -> tuple[Counter[str], Counter[str]]:
    tags: Counter[str] = Counter()
    pairs: Counter[str] = Counter()
    for row in rows:
        tagged = _tags(row)
        methods = tagged.get("methods", [])
        domains = tagged.get("domains", [])
        tasks = tagged.get("tasks", [])
        tags.update(set(methods + domains + tasks))
        for domain in domains:
            pairs.update(f"{domain} × {item}" for item in methods + tasks)
    return tags, pairs


def weekly_signal_metrics(
    current_rows: list[dict], baseline_rows: list[dict], baseline_days: int = 28
) -> dict:
    current_tags, current_pairs = _signal_counts(current_rows)
    baseline_tags, baseline_pairs = _signal_counts(baseline_rows)
    baseline_weeks = max(baseline_days / 7, 1)

    def comparison(current: Counter[str], baseline: Counter[str]) -> list[dict]:
        return [
            {
                "name": name,
                "current_count": current[name],
                "baseline_weekly_average": round(baseline[name] / baseline_weeks, 2),
                "newly_observed": current[name] > 0 and baseline[name] == 0,
            }
            for name in sorted(current.keys() | baseline.keys())
        ]

    return {
        "current_unique_papers": len(current_rows),
        "baseline_unique_papers": len(baseline_rows),
        "baseline_days": baseline_days,
        "tag_comparison": comparison(current_tags, baseline_tags),
        "combination_comparison": comparison(current_pairs, baseline_pairs),
    }


def _weekly_insight(item: WeeklyInsightSchema) -> WeeklyInsight:
    return WeeklyInsight(
        title=item.title,
        insight=item.insight,
        confidence=item.confidence,
        paper_ids=item.paper_ids,
    )


class OpenAIWeeklyTrendAnalyzer:
    def __init__(self, api_key: str, model: str):
        self.client = OpenAI(api_key=api_key)
        self.model = model

    def analyze(
        self,
        current_rows: list[dict],
        baseline_rows: list[dict],
        days: int,
        collection_coverage: dict,
    ) -> WeeklyTrendAnalysis:
        baseline_days = 28
        payload = {
            "current_window_days": days,
            "baseline_window_days": baseline_days,
            "metrics": weekly_signal_metrics(current_rows, baseline_rows, baseline_days),
            "collection_coverage": collection_coverage,
            "current_papers": [
                _paper_record(row, MAX_ABSTRACT_CHARS)
                for row in current_rows[:MAX_WEEKLY_CURRENT_PAPERS]
            ],
            "baseline_papers": [
                _paper_record(row, MAX_WEEKLY_BASELINE_ABSTRACT_CHARS)
                for row in baseline_rows[:MAX_WEEKLY_BASELINE_PAPERS]
            ],
        }
        response = self.client.responses.parse(
            model=self.model,
            input=[
                {"role": "system", "content": WEEKLY_TREND_SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": "다음 JSON 데이터만 근거로 주간 분석을 작성하세요:\n"
                    + json.dumps(payload, ensure_ascii=False, default=str),
                },
            ],
            text_format=WeeklyTrendSchema,
        )
        parsed = response.output_parsed
        if parsed is None:
            raise RuntimeError("The model returned no structured weekly trend analysis")
        return WeeklyTrendAnalysis(
            research_pulse=parsed.research_pulse,
            emerging_signals=[_weekly_insight(item) for item in parsed.emerging_signals],
            cross_domain_convergence=[
                _weekly_insight(item) for item in parsed.cross_domain_convergence
            ],
            papers_worth_reading=[
                WeeklyPaperPick(paper_id=item.paper_id, role=item.role, why=item.why)
                for item in parsed.papers_worth_reading
            ],
            research_opportunities=[
                _weekly_insight(item) for item in parsed.research_opportunities
            ],
            watchlist=[_weekly_insight(item) for item in parsed.watchlist],
            data_coverage=parsed.data_coverage,
        )
