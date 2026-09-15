"""The envelope every lobe reads and writes. Prose is only allowed in `answer`."""
from typing import Literal

from pydantic import BaseModel, Field


class Observation(BaseModel):
    source: str                      # "tool:ocr", "lobe:perception"
    ref: str | None = None           # artifact id in the run dir
    summary: str


class ToolCall(BaseModel):
    name: str
    args: dict


class Confidence(BaseModel):
    score: float = Field(ge=0, le=1)
    basis: Literal["self", "consistency", "evidence", "logprob"] = "self"


class Next(BaseModel):
    action: Literal["tool", "answer"]
    module: str | None = None


class Envelope(BaseModel):
    kind: Literal["step_result", "final"]
    goal: str
    observations: list[Observation] = []
    tool_calls: list[ToolCall] = []
    answer: str | None = None
    uncertainties: list[str] = []
    confidence: Confidence = Confidence(score=0.5)
    next: Next


class Verdict(BaseModel):
    verdict: Literal["PASS", "RETRY", "CONFLICT"]
    basis: Literal["evidence", "consistency", "none"] = "none"   # what a PASS rests on; code fills this, never the model
    failed_claims: list[str] = []
    notes: str = ""


def json_schema(model: type[BaseModel]) -> dict:
    return model.model_json_schema()
