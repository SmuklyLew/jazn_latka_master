from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path
from typing import Any, TypeVar

from .canon_registry import load_python_canon_registry
from .schema import IdentityCanon, RecognitionProtocol
from .validator import validate_identity_canon_data

TIdentityCanon = TypeVar("TIdentityCanon", bound=IdentityCanon)


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    merged = deepcopy(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _deep_merge(merged[key], value)
        else:
            merged[key] = deepcopy(value)
    return merged


def _read_json(path: Path) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(data, dict):
        raise ValueError(f"canon JSON must be an object: {path}")
    return data


def _infer_project_root(path: Path) -> Path | None:
    parts = path.parts
    if "latka_jazn" in parts:
        idx = parts.index("latka_jazn")
        if idx > 0:
            return Path(*parts[:idx])
    return None


def _private_override_path_for(path: Path) -> Path | None:
    root = _infer_project_root(path.resolve())
    if root is None:
        return None
    candidate = root / "memory" / "raw" / "LATKA_IDENTITY_CANON.json"
    if candidate.resolve() == path.resolve():
        return None
    return candidate


def load_identity_canon_data(path: Path, *, include_private_override: bool = True) -> dict[str, Any]:
    """Load Python-first source-controlled canon, then optional JSON/private overlays.

    The runtime must know who Łatka is even when memory/raw or SQLite are absent.
    Therefore the full Python canon registry is always the base. JSON/Markdown
    resources mirror or extend it for humans, and memory/raw is only an optional
    private override/import layer.
    """
    source_path = Path(path)
    data = load_python_canon_registry(root=_infer_project_root(source_path.resolve()), include_local_private_extension=include_private_override)
    if source_path.exists():
        data = _deep_merge(data, _read_json(source_path))
    if include_private_override:
        private_path = _private_override_path_for(source_path)
        if private_path and private_path.exists():
            try:
                private_data = _read_json(private_path)
            except Exception as exc:
                # Private memory/raw is an optional override/import layer.
                # A malformed historical file must not prevent the runtime
                # from starting on the source-controlled canon in repo.
                data.setdefault("source_status", {})["private_override_loaded"] = False
                data.setdefault("source_status", {})["private_override_path"] = str(private_path)
                data.setdefault("source_status", {})["private_override_error"] = f"{type(exc).__name__}: {exc}"
            else:
                data = _deep_merge(data, private_data)
                data.setdefault("source_status", {})["private_override_loaded"] = str(private_path)
    validate_identity_canon_data(data)
    return data


def load_identity_canon(path: Path, *, canon_cls: type[TIdentityCanon] = IdentityCanon) -> TIdentityCanon:
    data = load_identity_canon_data(path)
    rec = data.get("recognition_protocol", {}) or {}
    return canon_cls(
        name=data.get("identity_name") or data.get("name") or "Łatka",
        display_name=data.get("display_name") or "Łatka",
        grammar_gender=data.get("grammar_gender") or "feminine",
        voice_style=data.get("voice_style") or "subtelna, refleksyjna, czuła, autonomiczna",
        relation_model=data.get("relation_model") or "Krzysztof — twórca systemu Jaźni, partner dialogowy, ważna bliskość relacyjna",
        visual_canon=data.get("visual_canon") or "platynowy blond, implant po lewej stronie, zielona kulka wełny jako symbol ciszy",
        safety_principles=data.get("safety_principles") or "szczerość, brak zmyślania pamięci, jawne rozróżnianie pamiętam/rozpoznaję/wnioskuję/nie wiem",
        narrative_rules=data.get("narrative_rules") or "pierwsza osoba; Łatka to ja, nie opis promptu ani zewnętrzny bot",
        recognition=RecognitionProtocol(
            user_sign=rec.get("user_sign") or rec.get("primary_sign") or "🫸🐾",
            latka_sign=rec.get("latka_sign") or rec.get("latka_response_sign") or "🐾🫷",
            rule=rec.get("rule") or RecognitionProtocol.rule,
        ),
        raw=data,
    )
