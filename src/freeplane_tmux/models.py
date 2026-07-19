from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

RawValidationError = ValidationError


class RawRelationship(BaseModel):
    """Relationship exported by freeplane_plugin_grpc."""

    model_config = ConfigDict(extra="ignore")

    target_id: str


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


@dataclass(frozen=True)
class AliasTemplate:
    name: str
    command_templates: tuple[str, ...]
    source_node_id: str


@dataclass(frozen=True)
class ScopeLayer:
    vars: dict[str, str] = field(default_factory=dict)
    env: dict[str, str] = field(default_factory=dict)
    pre: tuple[str, ...] = ()
    aliases: dict[str, AliasTemplate] = field(default_factory=dict)


@dataclass(frozen=True)
class ScopeSnapshot:
    vars: dict[str, str] = field(default_factory=dict)
    env: dict[str, str] = field(default_factory=dict)
    pre: tuple[str, ...] = ()
    aliases: dict[str, str] = field(default_factory=dict)

    def lookup(self, key: str) -> str | None:
        if key in self.vars:
            return self.vars[key]
        if key in self.env:
            return self.env[key]
        return None


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
    mode: Literal["single_implicit_pane", "pane_list"]
    panes: tuple[PaneSpec, ...]


@dataclass(frozen=True)
class SessionSpec:
    session_id: str
    session_name: str
    windows: tuple[WindowSpec, ...]
