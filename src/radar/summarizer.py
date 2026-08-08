from __future__ import annotations

from openai import OpenAI
from pydantic import BaseModel, Field

from radar.models import PaperSummary


class SummarySchema(BaseModel):
    paper: str = Field(description="Paper title")
    problem: str
    method: str
    benchmark: str
    why_it_matters: str
    can_i_use_it: str


SYSTEM_PROMPT = """You summarize research papers for a researcher interested in MARL, MEC,
SAGIN, NTN, UAVs, ISCC, URLLC, satellite routing and handover, digital twins,
physical AI, VLA, diffusion models, quantum AI, edge caching, and graph neural networks.

Use only the supplied title and abstract. Never invent experimental results, datasets,
benchmarks, code availability, or publication status. If a requested fact is absent, write
exactly 'Not stated in abstract'. Keep each field concise and concrete. For can_i_use_it,
identify at most one plausible connection to the researcher's domains and label it as a
hypothesis when the abstract does not validate that application.
"""


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

