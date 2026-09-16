"""What perception and the ocr engine write into a request's observations."""
from pydantic import BaseModel


class Observation(BaseModel):
    source: str                      # "tool:ocr", "lobe:perception"
    ref: str | None = None           # artifact id in the run dir
    summary: str
