from __future__ import annotations

import json

from openai import OpenAI
from pydantic import BaseModel, Field

from radar.models import MonthlyTrendAnalysis, PaperSummary, TrendEvidence, TrendSection


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

MAX_TREND_PAPERS = 100
MAX_ABSTRACT_CHARS = 1200


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
                    "tags": json.loads(row.get("tags_json") or "{}"),
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
