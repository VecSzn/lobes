"""The envelope every lobe reads and writes. Prose is only allowed in `answer`."""
from typing import Literal

from pydantic import BaseModel, Field


class Observation(BaseModel):
    source: str                      # "tool:python", "lobe:perception", "user"
    ref: str | None = None           # artifact id in the run dir
    summary: str


class Claim(BaseModel):
    id: str
    text: str
    support: Literal["tool", "derived", "assumed"]
    evidence: str | None = None      # ref of the observation backing it


class ToolCall(BaseModel):
    name: str
    args: dict


class Confidence(BaseModel):
    score: float = Field(ge=0, le=1)
    basis: Literal["self", "consistency", "evidence", "logprob"] = "self"


class Next(BaseModel):
    action: Literal["tool", "verify", "answer", "retry", "escalate"]
    module: str | None = None


class Envelope(BaseModel):
    kind: Literal["plan", "step_result", "tool_result", "verdict", "final"]
    goal: str
    observations: list[Observation] = []
    claims: list[Claim] = []
    tool_calls: list[ToolCall] = []
    answer: str | None = None
    uncertainties: list[str] = []
    confidence: Confidence = Confidence(score=0.5)
    next: Next


class Verdict(BaseModel):
    verdict: Literal["PASS", "RETRY", "VERIFY_WITH_TOOL", "CONFLICT"]
    failed_claims: list[str] = []
    proposed_check: ToolCall | None = None
    notes: str = ""


def json_schema(model: type[BaseModel]) -> dict:
    return model.model_json_schema()
