from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field, fields, is_dataclass
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

RawValidationError = ValidationError


class RelationshipKind(str, Enum):
    CALL = "call"
    INHERIT = "inherit"


class RawRelationship(BaseModel):
    """Relationship exported by freeplane_plugin_grpc."""

    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    target_id: str
    kind: str | None = None
    type: str | None = None
    label: str | None = None
    name: str | None = None

    @property
    def explicit_kind(self) -> RelationshipKind | None:
        raw = self.kind or self.type or self.label or self.name
        if raw is None:
            return None
        normalized = str(raw).strip().lower()
        if normalized in {"call", "inherit"}:
            return RelationshipKind(normalized)
        return None

    @property
    def declared_kind(self) -> str | None:
        raw = self.kind or self.type or self.label or self.name
        if raw is None:
            return None
        return str(raw).strip() or None


class RawNode(BaseModel):
    """Minimal Freeplane node shape needed by the compiler."""

    model_config = ConfigDict(extra="ignore")

    text: str = ""
    id: str
    children: list[RawNode] = Field(default_factory=list)
    detail: str | None = None
    tags: list[str] = Field(default_factory=list)
    relationships: list[RawRelationship] = Field(default_factory=list)
    attributes: dict[str, Any] = Field(default_factory=dict)
    folded: bool | None = None

    @model_validator(mode="after")
    def normalize_export_values(self) -> RawNode:
        self.text = "" if self.text is None else str(self.text)
        self.detail = None if self.detail is None else str(self.detail)
        self.tags = [str(tag) for tag in self.tags]
        self.attributes = {str(key): value for key, value in self.attributes.items()}
        return self


RawNode.model_rebuild()


class Diagnostic(BaseModel):
    model_config = ConfigDict(extra="forbid")

    severity: Literal["error", "warning", "info"]
    code: str
    message: str
    node_id: str | None = None
    node_path: str | None = None
    hint: str | None = None
    field: str | None = None
    template: str | None = None
    relationship_target_id: str | None = None
    source_node_id: str | None = None

    def to_json_dict(self) -> dict[str, Any]:
        return self.model_dump(exclude_none=True)


@dataclass(frozen=True)
class CompileResult:
    session: SessionSpec | None
    diagnostics: tuple[Diagnostic, ...] = ()
    explain_plan: dict[str, Any] | None = None

    @property
    def ok(self) -> bool:
        return not any(diagnostic.severity == "error" for diagnostic in self.diagnostics)

    @property
    def errors(self) -> tuple[Diagnostic, ...]:
        return tuple(d for d in self.diagnostics if d.severity == "error")

    @property
    def warnings(self) -> tuple[Diagnostic, ...]:
        return tuple(d for d in self.diagnostics if d.severity == "warning")

    def to_json_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "ok": self.ok,
            "diagnostics": [diagnostic.to_json_dict() for diagnostic in self.diagnostics],
        }
        if self.session is not None:
            payload["session"] = dataclass_to_dict(self.session)
        if self.explain_plan is not None:
            payload["explain"] = self.explain_plan
        return payload


@dataclass(frozen=True)
class AliasTemplate:
    name: str
    command_templates: tuple[str, ...]
    source_node_id: str


@dataclass(frozen=True)
class ScopeLayer:
    scoped_vars: dict[str, str] = field(default_factory=dict)
    env: dict[str, str] = field(default_factory=dict)
    pre: tuple[str, ...] = ()
    aliases: dict[str, AliasTemplate] = field(default_factory=dict)
    runtime_attrs: dict[str, str] = field(default_factory=dict)
    call_args: dict[str, str] = field(default_factory=dict)
    helper_defaults: dict[str, str] = field(default_factory=dict)
    tmux_mode: str | None = None
    tmux_layout: str | None = None


@dataclass(frozen=True)
class ScopeSnapshot:
    vars: dict[str, str] = field(default_factory=dict)
    lists: dict[str, tuple[str, ...]] = field(default_factory=dict)
    object_fields: dict[str, tuple[str, ...]] = field(default_factory=dict)
    env: dict[str, str] = field(default_factory=dict)
    pre: tuple[str, ...] = ()
    aliases: dict[str, str] = field(default_factory=dict)
    lookup_value: Callable[[str], Any | None] | None = None

    def lookup(self, key: str) -> Any | None:
        if key in self.vars:
            return self.vars[key]
        if key in self.lists:
            return self.lists[key]
        if key in self.env:
            return self.env[key]
        if self.lookup_value is None:
            return None
        return self.lookup_value(key)


@dataclass(frozen=True)
class CommandStep:
    node_id: str
    display_name: str
    payload_source: Literal["text", "detail", "relationship"]
    command: str
    effective_scope: ScopeSnapshot


@dataclass(frozen=True)
class PaneSpec:
    pane_id: str
    title: str | None
    base_scope: ScopeSnapshot
    steps: tuple[CommandStep, ...]


@dataclass(frozen=True)
class WindowSpec:
    window_id: str
    name: str
    mode: Literal["single_implicit_pane", "pane_list", "mixed"]
    layout: str | None
    panes: tuple[PaneSpec, ...]


@dataclass(frozen=True)
class SessionSpec:
    session_id: str
    session_name: str
    start_directory: str | None
    windows: tuple[WindowSpec, ...]


def dataclass_to_dict(value: Any) -> Any:
    if is_dataclass(value):
        result: dict[str, Any] = {}
        for dataclass_field in fields(value):
            item = getattr(value, dataclass_field.name)
            if dataclass_field.name == "lookup_value":
                continue
            result[dataclass_field.name] = dataclass_to_dict(item)
        return result
    if isinstance(value, tuple):
        return [dataclass_to_dict(item) for item in value]
    if isinstance(value, list):
        return [dataclass_to_dict(item) for item in value]
    if isinstance(value, dict):
        return {key: dataclass_to_dict(item) for key, item in value.items()}
    return value
