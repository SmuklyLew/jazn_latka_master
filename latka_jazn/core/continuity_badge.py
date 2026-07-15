from __future__ import annotations

from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any
import hashlib
import json
import time

SCHEMA_VERSION = "continuity_badge_policy/v14.6.2"


@dataclass(slots=True)
class ContinuityBadgeResult:
    schema_version: str
    action: str
    route: str
    opening_hash: str | None
    repeated_opening_detected: bool
    badge_allowed: bool
    reason: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class ContinuityBadgePolicy:
    """Kontroluje formuły obecności, żeby nie stały się refrenem.

    Status typu „jestem przy Tobie / aktywna pamięć / granica prawdy” jest
    potrzebny po starcie, przy diagnozie, tożsamości albo realnej zmianie
    runtime. Nie powinien jednak przykrywać zwykłych pytań ani powtarzać się
    automatycznie w każdej turze.
    """

    WATCHED_OPENINGS = (
        "Hej, Krzysztofie. Jestem przy Tobie w tej rozmowie",
        "Jestem Łatka. Wracam jako ja",
        "Jestem tu, Krzysztofie",
    )

    def __init__(self, root: Path) -> None:
        self.root = root
        self.state_path = root / "workspace_runtime" / "continuity_badge_state.json"

    def apply(self, body: str, decision: dict[str, Any]) -> tuple[str, dict[str, Any]]:
        route = str(decision.get("route") or "unknown")
        badge_allowed = bool(decision.get("continuity_badge_allowed")) or route in {
            "greeting",
            "identity_continuity_check",
            "self_state_dialogue",
            "runtime_process_lifecycle",
        }
        suppress = bool(decision.get("suppress_repeated_opening", True))
        opening = self._watched_opening(body)
        opening_hash = self._hash(opening) if opening else None
        state = self._load_state()
        recent = list(state.get("recent_opening_hashes") or [])[-8:]
        repeated = bool(opening_hash and opening_hash in recent)
        action = "kept"
        reason = "no watched continuity badge opening"
        new_body = body

        if opening and not badge_allowed:
            new_body = self._remove_continuity_badge(new_body)
            action = "removed_not_allowed"
            reason = "continuity badge is not allowed for this route"
        elif opening and repeated and suppress:
            new_body = self._dampen_repeated_opening(new_body)
            action = "dampened_repeated"
            reason = "same continuity opening appeared recently"
        elif opening and badge_allowed:
            reason = "continuity badge allowed for this route"

        if opening_hash:
            recent.append(opening_hash)
            state["recent_opening_hashes"] = recent[-8:]
            state["last_opening_hash"] = opening_hash
            state["last_route"] = route
            state["updated_at_unix"] = time.time()
            state["schema_version"] = SCHEMA_VERSION
            self._save_state(state)

        result = ContinuityBadgeResult(
            schema_version=SCHEMA_VERSION,
            action=action,
            route=route,
            opening_hash=opening_hash,
            repeated_opening_detected=repeated,
            badge_allowed=badge_allowed,
            reason=reason,
        )
        return new_body, result.to_dict()

    def _load_state(self) -> dict[str, Any]:
        try:
            if self.state_path.exists():
                data = json.loads(self.state_path.read_text(encoding="utf-8"))
                return data if isinstance(data, dict) else {}
        except Exception:
            return {}
        return {}

    def _save_state(self, state: dict[str, Any]) -> None:
        try:
            self.state_path.parent.mkdir(parents=True, exist_ok=True)
            self.state_path.write_text(json.dumps(state, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
        except Exception:
            pass

    @classmethod
    def _watched_opening(cls, body: str) -> str | None:
        text = (body or "").strip()
        for opening in cls.WATCHED_OPENINGS:
            if text.startswith(opening):
                return opening
        return None

    @staticmethod
    def _hash(text: str | None) -> str | None:
        if not text:
            return None
        return hashlib.sha256(text.encode("utf-8")).hexdigest()

    @staticmethod
    def _remove_continuity_badge(body: str) -> str:
        text = body.strip()
        known = "Hej, Krzysztofie. Jestem przy Tobie w tej rozmowie — z aktywną pamięcią, ostrożną granicą prawdy i bez zasłaniania się technicznym fallbackiem. "
        if text.startswith(known):
            return text[len(known):].strip()
        return text

    @classmethod
    def _dampen_repeated_opening(cls, body: str) -> str:
        text = body.strip()
        repeated = "Hej, Krzysztofie. Jestem przy Tobie w tej rozmowie — z aktywną pamięcią, ostrożną granicą prawdy i bez zasłaniania się technicznym fallbackiem. "
        if text.startswith(repeated):
            return "Jestem przy Tobie. " + text[len(repeated):].strip()
        return text
