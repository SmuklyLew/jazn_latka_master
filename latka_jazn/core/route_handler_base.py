from __future__ import annotations
from dataclasses import asdict, dataclass, field
from typing import Any, Protocol

SCHEMA_VERSION = "route_handler_base/v14.6.10"

@dataclass(slots=True)
class RouteHandlerResult:
    handler_name: str
    route: str
    body: str
    intent: str = "unknown"
    data: dict[str, Any] = field(default_factory=dict)
    sources: list[dict[str, Any]] = field(default_factory=list)
    response_generation_mode_hint: str = "runtime_dynamic"
    generation_mode: str = "handler_generated"
    required_components: list[str] = field(default_factory=list)
    satisfied_components: list[str] = field(default_factory=list)
    missing_components: list[str] = field(default_factory=list)
    source_origin_detail: str = "handler_generated"
    template_origin: dict[str, Any] | None = None
    memory_sources: list[dict[str, Any]] = field(default_factory=list)
    file_sources: list[dict[str, Any]] = field(default_factory=list)
    dictionary_sources: list[dict[str, Any]] = field(default_factory=list)
    confidence: float = 0.0
    truth_boundary: str = "Handler generuje odpowiedź w runtime; jeśli korzysta z szablonu, musi oddać template_origin."
    errors: list[dict[str, Any]] = field(default_factory=list)
    schema_version: str = SCHEMA_VERSION
    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

class RouteHandler(Protocol):
    name: str
    route: str
    handled_intents: tuple[str, ...]
    def handle(self, text: str, context: dict[str, Any]) -> RouteHandlerResult: ...
