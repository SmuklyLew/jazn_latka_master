from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import secrets
from typing import Any, Iterable

from latka_jazn.core.text_io_contract import write_utf8_atomic
from latka_jazn.version import schema_version

SCHEMA_VERSION = schema_version("private_data_export_gate")
PRIVATE_PROFILES = {"memory", "full"}
SOURCE_SAFE_PROFILES = {"source-safe", "github_source_safe"}
_PRIVATE_MARKER_PARTS = (
    ("local_private", "do_not_commit_without_review"),
    ("generated_from", "private_memory"),
    ("raw_conversation", "embedded_source"),
)
_PRIVATE_EXACT_SUFFIXES = {
    "latka_jazn/core/canon/local_private_canon_extension.py",
    "latka_jazn/contracts/embedded_sources.py",
}
_TEXT_SCAN_SUFFIXES = {".py", ".json", ".jsonl", ".md", ".txt", ".yaml", ".yml", ".toml"}


@dataclass(slots=True)
class ExportItem:
    path: str
    size_bytes: int
    data_class: str
    risk: str
    evidence: list[str] = field(default_factory=list)


@dataclass(slots=True)
class PrivateExportPreview:
    profile: str
    items: list[ExportItem]
    total_bytes: int
    plan_hash: str
    requires_confirmation: bool
    blocked: bool = False
    blocked_items: list[str] = field(default_factory=list)
    risks: list[str] = field(default_factory=list)
    schema_version: str = SCHEMA_VERSION
    truth_boundary: str = (
        "Podgląd klasyfikuje dokładny plan eksportu. Profil source-safe jest blokowany, "
        "gdy plan zawiera sekret, bazę, surową rozmowę, pamięć albo źródło wygenerowane z prywatnej pamięci. "
        "Profile memory/full wymagają jednorazowego potwierdzenia, ale nie zmienia to klasyfikacji danych."
    )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class PrivateDataExportGate:
    def __init__(self, token_store: Path | str) -> None:
        self.token_store = Path(token_store)

    @staticmethod
    def _private_content_markers(path: Path) -> list[str]:
        normalized = path.as_posix().lower().lstrip("./")
        evidence: list[str] = []
        if any(normalized.endswith(suffix) for suffix in _PRIVATE_EXACT_SUFFIXES):
            evidence.append("known_private_generated_source")
        if path.suffix.lower() not in _TEXT_SCAN_SUFFIXES or not path.is_file():
            return evidence
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")[:262144].lower()
        except OSError:
            evidence.append("privacy_scan_unreadable")
            return evidence
        for left, right in _PRIVATE_MARKER_PARTS:
            marker = left + "_" + right
            if marker in text:
                evidence.append(f"private_marker:{marker}")
        return sorted(set(evidence))

    @classmethod
    def _classify(cls, path: Path) -> tuple[str, str, list[str]]:
        lower = path.as_posix().lower()
        evidence = cls._private_content_markers(path)
        if any(item.startswith("known_private_generated_source") or item.startswith("private_marker:") for item in evidence):
            return "private_generated_source", "critical", evidence
        if lower.endswith((".key", ".pem", ".p12", ".pfx", ".env")) or path.name.lower() in {"id_rsa", "id_ed25519"}:
            return "secret_candidate", "critical", evidence or ["secret_filename_or_suffix"]
        if "raw_chat" in lower or lower.endswith((".html", ".jsonl")):
            return "raw_conversation", "high", evidence
        if lower.endswith((".sqlite", ".sqlite3", ".db")):
            return "database", "high", evidence
        if "memory" in Path(lower).parts or "/memory/" in lower:
            return "memory", "high", evidence
        if "privacy_scan_unreadable" in evidence:
            return "unreadable_source", "high", evidence
        return "project_data", "medium", evidence

    def preview(self, *, profile: str, paths: Iterable[Path | str]) -> PrivateExportPreview:
        normalized_profile = str(profile).strip().lower()
        items: list[ExportItem] = []
        for value in paths:
            path = Path(value)
            size = path.stat().st_size if path.is_file() else 0
            data_class, risk, evidence = self._classify(path)
            items.append(ExportItem(str(path), size, data_class, risk, evidence))
        canonical = [asdict(item) for item in sorted(items, key=lambda item: item.path)]
        plan_hash = hashlib.sha256(
            json.dumps(
                {"profile": normalized_profile, "items": canonical},
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
        risky = [item for item in items if item.risk in {"high", "critical"}]
        source_safe_blocked = normalized_profile in SOURCE_SAFE_PROFILES and bool(risky)
        return PrivateExportPreview(
            profile=normalized_profile,
            items=items,
            total_bytes=sum(item.size_bytes for item in items),
            plan_hash=plan_hash,
            requires_confirmation=(normalized_profile in PRIVATE_PROFILES and not source_safe_blocked),
            blocked=source_safe_blocked,
            blocked_items=[item.path for item in risky],
            risks=sorted({item.risk for item in risky}),
        )

    def _load(self) -> dict[str, Any]:
        if not self.token_store.exists():
            return {"tokens": {}}
        try:
            return json.loads(self.token_store.read_text(encoding="utf-8"))
        except Exception:
            return {"tokens": {}}

    def _save(self, payload: dict[str, Any]) -> None:
        write_utf8_atomic(self.token_store, json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n")

    def issue_confirmation(self, preview: PrivateExportPreview, *, ttl_seconds: int = 900) -> str:
        if preview.blocked:
            return "blocked"
        if not preview.requires_confirmation:
            return "not-required"
        token = secrets.token_urlsafe(32)
        token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
        now = datetime.now(timezone.utc)
        payload = self._load()
        tokens = payload.setdefault("tokens", {})
        tokens[token_hash] = {
            "plan_hash": preview.plan_hash,
            "profile": preview.profile,
            "created_at_utc": now.isoformat(),
            "expires_at_utc": (now + timedelta(seconds=ttl_seconds)).isoformat(),
            "used": False,
        }
        self._save(payload)
        return token

    def consume_confirmation(self, *, token: str, preview: PrivateExportPreview) -> dict[str, Any]:
        if preview.blocked:
            return {
                "allowed": False,
                "reason": "source_safe_contains_private_or_high_risk_data",
                "blocked_items": list(preview.blocked_items),
                "plan_hash": preview.plan_hash,
            }
        if not preview.requires_confirmation:
            return {"allowed": True, "reason": "confirmation_not_required", "plan_hash": preview.plan_hash}
        token_hash = hashlib.sha256(str(token).encode("utf-8")).hexdigest()
        payload = self._load()
        record = payload.get("tokens", {}).get(token_hash)
        if not record:
            return {"allowed": False, "reason": "confirmation_token_unknown"}
        if record.get("used"):
            return {"allowed": False, "reason": "confirmation_token_replayed"}
        if record.get("plan_hash") != preview.plan_hash or record.get("profile") != preview.profile:
            return {"allowed": False, "reason": "confirmation_plan_changed"}
        expires = datetime.fromisoformat(str(record["expires_at_utc"]).replace("Z", "+00:00"))
        if datetime.now(timezone.utc) > expires:
            return {"allowed": False, "reason": "confirmation_token_expired"}
        record["used"] = True
        record["used_at_utc"] = datetime.now(timezone.utc).isoformat()
        self._save(payload)
        return {
            "allowed": True,
            "reason": "private_export_confirmed",
            "plan_hash": preview.plan_hash,
            "confirmation_evidence": token_hash,
        }
