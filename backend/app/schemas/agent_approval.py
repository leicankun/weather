"""Only the server chooses operation parameters; clients submit an explicit decision."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, StrictBool


class ProposedAction(BaseModel):
    model_config = ConfigDict(extra="forbid")
    operation: Literal["delete_conversation", "clear_memory"]
    target_conversation_id: str | None = Field(default=None, min_length=1, max_length=64)


class ActionProposal(BaseModel):
    actions: list[ProposedAction] = Field(default_factory=list, max_length=5)
    clarification: str = Field(default="", max_length=500)


class ApprovalDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")
    approval_id: str = Field(min_length=32, max_length=32, pattern=r"^[0-9a-f]{32}$")
    approved: StrictBool
