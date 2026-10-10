"""卡片增量提取契约：必须包含用户原话证据。"""

from typing import Literal

from pydantic import BaseModel, Field


class CardPatch(BaseModel):
    kind: Literal["preference", "fact"]
    key: str = Field(min_length=1, max_length=80, pattern=r"^[a-z][a-z0-9_]*$")
    value: str = Field(min_length=1, max_length=300)
    evidence: str = Field(min_length=2, max_length=500)


class CardExtraction(BaseModel):
    cards: list[CardPatch] = Field(default_factory=list, max_length=8)


class MemorySearch(BaseModel):
    query: str = Field(min_length=1, max_length=1000)
    top_k: int = Field(default=3, ge=1, le=10)
