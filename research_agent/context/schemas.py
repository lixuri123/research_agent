"""Serializable contracts for context routing and source assembly."""
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

Route = Literal["independent", "followup", "task", "multi_task"]


class ContextOptions(BaseModel):
    model_config = ConfigDict(extra="forbid")
    mode: Literal["auto", "independent", "followup", "task", "multi_task"] = "auto"
    task_ids: list[str] = Field(default_factory=list)
    profile_ids: list[str] = Field(default_factory=list)
    turn_ids: list[str] = Field(default_factory=list)

    @field_validator("task_ids", "profile_ids", "turn_ids", mode="before")
    @classmethod
    def normalize_ids(cls, value):
        if not isinstance(value, (list, tuple)) or len(value) > 10:
            raise ValueError("references must be an array with at most 10 IDs")
        result = []
        for item in value:
            if not isinstance(item, str) or not item.strip() or len(item.strip()) > 255:
                raise ValueError("IDs must be nonempty strings of at most 255 characters")
            item = item.strip()
            if item not in result:
                result.append(item)
        return result

    @model_validator(mode="after")
    def validate_mode(self):
        if self.mode == "independent" and (self.task_ids or self.turn_ids):
            raise ValueError("independent cannot reference tasks or turns")
        if self.mode == "task" and len(self.task_ids) > 1:
            raise ValueError("task accepts at most one task ID")
        if self.mode == "multi_task" and len(self.task_ids) == 1:
            raise ValueError("multi_task requires at least two task IDs or explicit enumeration")
        return self


class ContextRequest(BaseModel):
    question: str
    requirements: str = ""
    owner_id: str
    session_id: str | None = None
    workspace_id: str | None = None
    project_id: str | None = None
    submitted_at: float
    run_id: str
    context: ContextOptions = Field(default_factory=ContextOptions)


class ContextIndex(BaseModel):
    turns: list[dict[str, Any]] = Field(default_factory=list)
    tasks: list[dict[str, Any]] = Field(default_factory=list)
    active_tasks: list[dict[str, Any]] = Field(default_factory=list)
    active_overflow: bool = False


class RouteDecision(BaseModel):
    model_config = ConfigDict(frozen=True)
    route: Route
    rule_id: str
    task_ids: tuple[str, ...] = ()
    turn_ids: tuple[str, ...] = ()
    reason_codes: tuple[str, ...] = ()
    needs_clarification: bool = False
    clarification: str | None = None


class SourceBlock(BaseModel):
    kind: Literal["history", "profile", "memory", "task"]
    id: str
    version: int | str | None = None
    scope: str = "person"
    scope_id: str | None = None
    content: str
    metadata: dict[str, Any] = Field(default_factory=dict)
    priority: int = 0
    truncated: bool = False


class SourceSnapshot(BaseModel):
    blocks: list[SourceBlock] = Field(default_factory=list)
    omitted: list[dict[str, Any]] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class RuntimeContext(BaseModel):
    system: str
    instruction: str = ""
    plan: str = ""
    observations: str = ""
    children: str = ""
    contract: str = ""


class ContextBudget(BaseModel):
    input_limit: int = Field(default=12000, gt=0)
    output_reserve: int = Field(default=4096, ge=0)
    history_limit: int = Field(default=3000, ge=0)
    profile_limit: int = Field(default=1000, ge=0)
    memory_limit: int = Field(default=2000, ge=0)
    task_limit: int = Field(default=3000, ge=0)
