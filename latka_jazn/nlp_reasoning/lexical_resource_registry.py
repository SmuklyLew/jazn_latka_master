from __future__ import annotations

import importlib.util
import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from latka_jazn.nlp_reasoning.lexical_resource_cache import LexicalResourceCache


LEXICAL_RESOURCE_REGISTRY_SCHEMA = "lexical_resource_registry/v14.8.4.005"


@dataclass(slots=True)
class LexicalResourceStatus:
    source_id: str
    available: bool
    mode: str
    license: str | None
    source_url: str | None
    data_path: str | None
    cache_entries: int
    reason: str | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": "lexical_resource_status/v14.8.4.005",
            "source_id": self.source_id,
            "available": self.available,
            "mode": self.mode,
            "license": self.license,
            "source_url": self.source_url,
            "data_path": self.data_path,
            "cache_entries": self.cache_entries,
            "reason": self.reason,
            "truth_boundary": "available=True oznacza tylko lokalną dostępność danego wpisu albo obecność wpisów cache; nie oznacza pobrania pełnej zewnętrznej bazy do repozytorium.",
        }


class LexicalResourceRegistry:
    """Rejestr małych metadanych i statusów zasobów leksykalnych/NLP.

    Ten moduł rozdziela trzy rzeczy, które wcześniej łatwo było mieszać:
    wpis w rejestrze, realną dostępność lokalnego zasobu oraz cache lookupów.
    """

    def __init__(
        self,
        root: str | Path | None = None,
        *,
        verified_sources_path: str | Path | None = None,
        project_lexicon_path: str | Path | None = None,
        cache_path: str | Path | None = None,
    ) -> None:
        self.root = Path(root) if root else Path.cwd()
        self.package_root = self._package_root(self.root)
        self.verified_sources_path = Path(verified_sources_path) if verified_sources_path else self.package_root / "resources" / "nlp" / "verified_sources.json"
        self.project_lexicon_path = Path(project_lexicon_path) if project_lexicon_path else self.package_root / "resources" / "nlp" / "latka_project_lexicon.json"
        self.cache = LexicalResourceCache(self.root, path=cache_path)

    def load_verified_sources(self) -> dict[str, Any]:
        return self._load_json(
            self.verified_sources_path,
            fallback={
                "schema_version": "verified_lexical_sources/v14.8.4.005",
                "sources": {},
                "policy": {},
                "status": "missing_verified_sources_file",
                "path": str(self.verified_sources_path),
            },
        )

    def load_project_lexicon(self) -> dict[str, Any]:
        return load_latka_project_lexicon(self.root, path=self.project_lexicon_path)

    def status(self) -> list[LexicalResourceStatus]:
        data = self.load_verified_sources()
        sources = data.get("sources") or {}
        if isinstance(sources, list):
            sources = {str(item.get("source_id")): item for item in sources if isinstance(item, dict) and item.get("source_id")}
        cache_stats = self.cache.stats()
        entries_by_source = cache_stats.get("entries_by_source") or {}
        statuses: list[LexicalResourceStatus] = []
        for source_id in sorted(sources):
            item = sources.get(source_id) or {}
            statuses.append(self._status_for_source(str(source_id), item, entries_by_source))
        return statuses

    def require_license_review(self, source_id: str) -> bool:
        sources = self.load_verified_sources().get("sources") or {}
        item = sources.get(source_id) if isinstance(sources, dict) else None
        if not isinstance(item, dict):
            return True
        if item.get("license_review_required") is True:
            return True
        text = " ".join(str(item.get(key, "")) for key in ("license", "redistribution", "mode"))
        folded = text.lower()
        return any(marker in folded for marker in ("manual", "review", "verify", "permission", "varies"))

    def to_dict(self) -> dict[str, Any]:
        verified = self.load_verified_sources()
        lexicon = self.load_project_lexicon()
        statuses = [item.to_dict() for item in self.status()]
        return {
            "schema_version": LEXICAL_RESOURCE_REGISTRY_SCHEMA,
            "verified_sources_path": str(self.verified_sources_path),
            "project_lexicon_path": str(self.project_lexicon_path),
            "source_count": len(statuses),
            "statuses": statuses,
            "policy": verified.get("policy", {}),
            "project_lexicon": {
                "schema_version": lexicon.get("schema_version"),
                "term_count": len(lexicon.get("terms") or {}),
                "status": lexicon.get("status", "loaded" if lexicon.get("terms") else "empty_or_missing"),
            },
            "cache": self.cache.stats(),
            "truth_boundary": verified.get(
                "truth_boundary",
                "Rejestr zasobów NLP odróżnia metadane, lokalną dostępność i cache; nie dowodzi pobrania pełnych zewnętrznych baz.",
            ),
        }

    def _status_for_source(self, source_id: str, item: dict[str, Any], entries_by_source: dict[str, int]) -> LexicalResourceStatus:
        mode = str(item.get("mode") or "unknown")
        data_path = self._resolve_data_path(item)
        cache_entries = int(entries_by_source.get(source_id, 0) or 0)
        available = False
        reason: str | None = None

        if source_id == "latka_project_lexicon":
            available = self.project_lexicon_path.exists()
            reason = None if available else "project lexicon file missing"
            data_path = self.project_lexicon_path
        elif source_id == "morfeusz2-sgjp":
            available = importlib.util.find_spec("morfeusz2") is not None
            reason = None if available else "morfeusz2 package not installed in current Python environment"
        elif source_id == "polimorf":
            data_path = self._polimorf_path(data_path)
            available = bool(data_path and data_path.exists() and data_path.is_file())
            reason = None if available else "PoliMorf local TSV/TAB not configured; set LATKA_POLIMORF_PATH after license review"
        elif data_path is not None:
            available = data_path.exists()
            reason = None if available else "configured data_path does not exist locally"
        elif mode.startswith("online") or "online" in mode:
            available = cache_entries > 0
            reason = None if available else "online/reference source registered; no cached lookup present in local cache"
        elif "external" in mode or "optional" in mode or "manual" in mode:
            available = cache_entries > 0
            reason = None if available else "optional external resource not installed or not cached locally"
        else:
            reason = "registered metadata only"

        return LexicalResourceStatus(
            source_id=source_id,
            available=available,
            mode=mode,
            license=item.get("license"),
            source_url=item.get("source_url") or item.get("url"),
            data_path=str(data_path) if data_path else item.get("data_path"),
            cache_entries=cache_entries,
            reason=reason,
        )

    def _resolve_data_path(self, item: dict[str, Any]) -> Path | None:
        raw = item.get("data_path") or item.get("local_path")
        if not raw:
            return None
        path = Path(str(raw))
        if path.is_absolute():
            return path
        return self.root / path

    def _polimorf_path(self, configured: Path | None) -> Path | None:
        env = os.environ.get("LATKA_POLIMORF_PATH")
        if env:
            return Path(env)
        if configured is not None:
            return configured
        candidates = [
            self.root / "external_data" / "polimorf" / "polimorf.tsv",
            self.root / "external_data" / "polimorf" / "polimorf.tab",
            self.root / "workspace_runtime" / "polish_reasoning" / "polimorf.tsv",
            self.root / "workspace_runtime" / "polish_reasoning" / "polimorf.tab",
        ]
        return next((candidate for candidate in candidates if candidate.exists()), candidates[0])

    def _load_json(self, path: Path, *, fallback: dict[str, Any]) -> dict[str, Any]:
        try:
            if not path.exists():
                return fallback
            data = json.loads(path.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else fallback
        except Exception as exc:
            out = dict(fallback)
            out["status"] = f"json_load_failed:{type(exc).__name__}"
            return out

    def _package_root(self, base: Path) -> Path:
        if (base / "resources").exists() and base.name == "latka_jazn":
            return base
        if (base / "latka_jazn").exists():
            return base / "latka_jazn"
        return base


def load_latka_project_lexicon(root: str | Path, path: str | Path | None = None) -> dict[str, Any]:
    base = Path(root)
    package_root = base / "latka_jazn" if (base / "latka_jazn").exists() else base
    lexicon_path = Path(path) if path else package_root / "resources" / "nlp" / "latka_project_lexicon.json"
    if not lexicon_path.exists():
        return {
            "schema_version": "latka_project_lexicon/v14.8.4.005",
            "terms": {},
            "status": "missing_project_lexicon_file",
            "path": str(lexicon_path),
        }
    try:
        data = json.loads(lexicon_path.read_text(encoding="utf-8"))
    except Exception as exc:
        return {
            "schema_version": "latka_project_lexicon/v14.8.4.005",
            "terms": {},
            "status": f"json_load_failed:{type(exc).__name__}",
            "path": str(lexicon_path),
        }
    data.setdefault("schema_version", "latka_project_lexicon/v14.8.4.005")
    data.setdefault("terms", {})
    return data
