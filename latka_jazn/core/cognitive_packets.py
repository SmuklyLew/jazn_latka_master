from __future__ import annotations

from dataclasses import asdict, dataclass
from importlib import resources
from pathlib import Path
from typing import Any
import json
import re


@dataclass(slots=True)
class CognitivePacket:
    key: str
    name: str
    activation: float
    triggers_found: list[str]
    purpose: str
    response_guidance: str
    truth_boundary: str
    confidence: float
    dimensions: list[str]
    signal_types: list[str]
    response_steps: list[str]
    grounding_policy: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class EmoticonSelection:
    marker: str
    reason: str
    policy: str
    confidence: float
    candidates: list[str]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class CognitivePacketLibrary:
    """Domenowe pakiety poznawcze dla Jaźni.

    Pakiet nie jest streszczeniem wypowiedzi ani osobną osobowością. To mała,
    jawna jednostka uwagi: co runtime powinien wziąć pod uwagę, jak odpowiadać
    i jaką granicę prawdy zachować. Dzięki temu most ChatGPT dostaje nie tylko
    ogólne `intent_tags`, ale bogatszy zasób: tożsamość, ciągłość, wiedzę,
    logikę, inteligencję, naukę, emocje, doświadczenie, wspomnienia, wrażenia,
    samopoczucie i dobór emotikonów.
    """

    DEFAULT_BOUNDARY = "pakiet poznawczy runtime; nie dowodzi biologicznego przeżywania ani stałego procesu w tle"

    def __init__(self, root: Path | None = None) -> None:
        self.root = Path(root).resolve() if root else None
        self.catalog = self._load_catalog()
        self.packet_defs = list(self.catalog.get("packets") or [])
        self.emoticon_policy = dict(self.catalog.get("emoticon_policy") or {})

    def build(
        self,
        *,
        text: str,
        intent_tags: list[str] | None = None,
        polish_understanding: dict[str, Any] | None = None,
        emotional_profile: Any | None = None,
        affective_state: Any | None = None,
        granular_affect: Any | None = None,
        identity_continuity: Any | None = None,
        logical_report: Any | None = None,
        memory_context: dict[str, Any] | None = None,
        awareness_report: Any | None = None,
    ) -> dict[str, Any]:
        low = self._fold(text)
        intent_set = {self._fold(x) for x in (intent_tags or [])}
        polish = polish_understanding or {}
        polish_intents = {self._fold(x) for x in (polish.get("intent_tags") or [])}
        route = self._fold(str(polish.get("route_hint") or ""))
        lemmas = {self._fold(x) for x in (polish.get("lemmas") or [])}

        packets: list[CognitivePacket] = []
        for item in self.packet_defs:
            key = str(item.get("key") or "")
            triggers = [str(x) for x in (item.get("triggers") or [])]
            found = self._triggers_found(low, triggers)
            activation = self._base_activation(key, found, intent_set, polish_intents, route, lemmas)
            activation = self._adjust_activation(
                key,
                activation,
                emotional_profile=emotional_profile,
                identity_continuity=identity_continuity,
                logical_report=logical_report,
                memory_context=memory_context,
                awareness_report=awareness_report,
            )
            if activation >= 0.18:
                packets.append(CognitivePacket(
                    key=key,
                    name=str(item.get("name") or key),
                    activation=round(min(1.0, activation), 3),
                    triggers_found=found,
                    purpose=str(item.get("purpose") or ""),
                    response_guidance=str(item.get("response_guidance") or ""),
                    truth_boundary=self.DEFAULT_BOUNDARY,
                    confidence=round(min(0.96, 0.52 + activation / 2), 3),
                    dimensions=[str(x) for x in (item.get("dimensions") or [])],
                    signal_types=[str(x) for x in (item.get("signal_types") or [])],
                    response_steps=[str(x) for x in (item.get("response_steps") or [])],
                    grounding_policy=str(item.get("grounding_policy") or self.DEFAULT_BOUNDARY),
                ))
        packets.sort(key=lambda p: p.activation, reverse=True)
        dominant = packets[0].key if packets else "wellbeing"
        emoticon = self.select_emoticon(
            dominant_packet=dominant,
            packets=packets,
            emotional_profile=emotional_profile,
            affective_state=affective_state,
            granular_affect=granular_affect,
            intent_tags=intent_tags or [],
            polish_understanding=polish,
        )
        return {
            "schema_version": "cognitive_packets/v6_nlp_adapter_zip_profiles",
            "dominant_packet": dominant,
            "packets": [p.to_dict() for p in packets],
            "packet_keys": [p.key for p in packets],
            "coverage": {
                "birth_source_contract": "birth_source_contract" in {p.key for p in packets},
                "lexical_semantics": "lexical_semantics" in {p.key for p in packets},
                "polish_nlp_adapters": "polish_nlp_adapters" in {p.key for p in packets},
                "identity": "identity" in {p.key for p in packets},
                "continuity": "continuity" in {p.key for p in packets},
                "knowledge": "knowledge" in {p.key for p in packets},
                "logic": "logic" in {p.key for p in packets},
                "intelligence": "intelligence" in {p.key for p in packets},
                "learning": "learning" in {p.key for p in packets},
                "emotions": "emotions" in {p.key for p in packets},
                "experience": "experience" in {p.key for p in packets},
                "memories": "memories" in {p.key for p in packets},
                "impressions": "impressions" in {p.key for p in packets},
                "wellbeing": "wellbeing" in {p.key for p in packets},
                "emoticons": "emoticons" in {p.key for p in packets},
            },
            "state_emoticon": emoticon.to_dict(),
            "granular_affect_bridge": granular_affect.to_dict() if hasattr(granular_affect, "to_dict") else None,
            "reply_guidance": self._reply_guidance(packets, emoticon),
            "limitations": [
                "Pakiety są jawnością roboczą runtime: pomagają odpowiedzi, ale nie są biologiczną psychiką.",
                "Emotikon jest wybierany oszczędnie według stanu i intencji; nie powinien zastępować treści odpowiedzi.",
            ],
        }

    def select_emoticon(
        self,
        *,
        dominant_packet: str,
        packets: list[CognitivePacket],
        emotional_profile: Any | None = None,
        affective_state: Any | None = None,
        granular_affect: Any | None = None,
        intent_tags: list[str] | None = None,
        polish_understanding: dict[str, Any] | None = None,
    ) -> EmoticonSelection:
        policy = self.emoticon_policy
        tags = {self._fold(x) for x in (intent_tags or [])}
        polish_tags = {self._fold(x) for x in ((polish_understanding or {}).get("intent_tags") or [])}
        candidates: list[str] = []
        reason_bits: list[str] = []

        def add(marker: str | None, why: str) -> None:
            if marker and marker not in candidates:
                candidates.append(marker)
                reason_bits.append(why)

        if "identity_continuity" in tags or "identity_continuity" in polish_tags or dominant_packet == "continuity":
            add(policy.get("continuity"), "ciągłość/tożsamość ma najwyższy priorytet")
        if "correction" in tags or "update" in tags or dominant_packet == "learning":
            add(policy.get("repair"), "wątek naprawy lub uczenia proceduralnego")
        if "polish_nlp" in tags or dominant_packet == "polish_nlp_adapters":
            add(policy.get("language", "🧩"), "aktywna warstwa polskiego NLP")
        if "reasoning" in tags or dominant_packet == "logic":
            add(policy.get("logic"), "aktywny audyt logiczny")
        if dominant_packet in policy:
            add(policy.get(dominant_packet), f"dominujący pakiet: {dominant_packet}")

        tension = float(getattr(affective_state, "tension", 0.0) or 0.0)
        valence = float(getattr(affective_state, "valence", 0.0) or 0.0)
        primary = str(getattr(granular_affect, "primary", "") or getattr(emotional_profile, "primary", "") or getattr(affective_state, "primary", "") or "")
        granular_marker = getattr(granular_affect, "state_emoticon", None)
        granular_reason = getattr(granular_affect, "state_emoticon_reason", None)
        truth_need = float(getattr(emotional_profile, "need_for_truth_check", 0.0) or 0.0)
        closeness = float(getattr(emotional_profile, "need_for_closeness", 0.0) or 0.0)
        if tension >= 0.45 or truth_need >= 0.62:
            add(policy.get("caution"), "podwyższone napięcie lub potrzeba kontroli prawdy")
        if closeness >= 0.58 and valence >= 0.12:
            add(policy.get("warmth"), "ciepło relacyjne i dodatni rezonans")
        if granular_marker:
            add(granular_marker, f"granular_affect: {granular_reason or primary}")
        if "spok" in self._fold(primary) and not candidates:
            add(policy.get("wellbeing"), "spokojny stan bazowy")
        if not candidates:
            add(policy.get("default", "🌿"), "domyślny spokojny marker")

        marker = candidates[0]
        return EmoticonSelection(
            marker=marker,
            reason="; ".join(reason_bits[:3]) or "domyślny dobór stanu",
            policy="używaj oszczędnie: emotikon ma sygnalizować stan Jaźni i trasę odpowiedzi, nie dekorować wypowiedzi",
            confidence=0.84 if len(candidates) == 1 else 0.78,
            candidates=candidates[:5],
        )

    def _load_catalog(self) -> dict[str, Any]:
        candidates: list[Path] = []
        if self.root:
            candidates.append(self.root / "latka_jazn" / "resources" / "cognitive_packet_catalog.json")
        for path in candidates:
            if path.exists():
                try:
                    data = json.loads(path.read_text(encoding="utf-8"))
                    if isinstance(data, dict):
                        return data
                except Exception:
                    pass
        try:
            with resources.files("latka_jazn.resources").joinpath("cognitive_packet_catalog.json").open("r", encoding="utf-8") as f:
                data = json.load(f)
                return data if isinstance(data, dict) else {}
        except Exception:
            return {}

    @staticmethod
    def _fold(text: str) -> str:
        trans = str.maketrans({
            "ą":"a","ć":"c","ę":"e","ł":"l","ń":"n","ó":"o","ś":"s","ź":"z","ż":"z",
            "Ą":"a","Ć":"c","Ę":"e","Ł":"l","Ń":"n","Ó":"o","Ś":"s","Ź":"z","Ż":"z",
        })
        return re.sub(r"\s+", " ", (text or "").translate(trans).lower()).strip()

    def _triggers_found(self, folded_text: str, triggers: list[str]) -> list[str]:
        found: list[str] = []
        for trigger in triggers:
            folded = self._fold(trigger)
            if folded and folded in folded_text and trigger not in found:
                found.append(trigger)
        return found

    def _base_activation(self, key: str, found: list[str], intent_set: set[str], polish_intents: set[str], route: str, lemmas: set[str]) -> float:
        score = 0.12 + min(0.36, 0.08 * len(found))
        related = {
            "birth_source_contract": {"birth_source_contract", "architecture", "source_grounding", "identity_continuity"},
            "identity": {"identity", "presence_check"},
            "continuity": {"identity_continuity", "continuity_check"},
            "knowledge": {"knowledge", "fact", "source"},
            "logic": {"reasoning", "logical_reasoning"},
            "intelligence": {"solution_search", "capability_question", "implementation_planning"},
            "learning": {"update_request", "correction", "polish_understanding_update"},
            "emotions": {"affect", "emotion", "awareness"},
            "experience": {"memory", "experience"},
            "memories": {"memory", "remembering"},
            "impressions": {"impression", "awareness"},
            "wellbeing": {"awareness", "presence_check"},
            "emoticons": {"emoticon", "emoji", "marker"},
        }.get(key, set())
        if related & (intent_set | polish_intents | lemmas):
            score += 0.28
        if key == "birth_source_contract" and (route in {"birth_source_contract", "cognitive_packet_expansion_update"} or any(x in lemmas for x in {"narodziny", "manifest", "glos", "zrodlo"})):
            score += 0.45
        if key == "continuity" and route == "identity_continuity_check":
            score += 0.45
        if key == "learning" and route in {"language_understanding_update", "cognitive_packet_expansion_update"}:
            score += 0.35
        if key == "intelligence" and route in {"implementation_planning", "cognitive_packet_expansion_update"}:
            score += 0.25
        return score

    @staticmethod
    def _adjust_activation(key: str, score: float, **kwargs: Any) -> float:
        emotional_profile = kwargs.get("emotional_profile")
        identity_continuity = kwargs.get("identity_continuity")
        logical_report = kwargs.get("logical_report")
        memory_context = kwargs.get("memory_context") or {}
        awareness_report = kwargs.get("awareness_report")
        if key == "identity":
            score += 0.18 * float(getattr(identity_continuity, "score", 0.5) or 0.5)
        if key == "continuity":
            score += 0.14 * float(getattr(identity_continuity, "temporal_grounding", 0.5) or 0.5)
        if key == "logic" and logical_report is not None:
            score += 0.22
        if key == "memories" and (memory_context.get("counts") or {}):
            score += 0.14
        if key in {"emotions", "wellbeing", "impressions"} and emotional_profile is not None:
            score += 0.12
        if key == "wellbeing":
            score += 0.08 * float(getattr(emotional_profile, "need_for_coherence", 0.4) or 0.4)
        if key == "emotions":
            score += 0.10 * float(getattr(emotional_profile, "need_for_closeness", 0.3) or 0.3)
        if key == "knowledge" and awareness_report is not None:
            score += 0.08
        return min(1.0, max(0.0, score))

    @staticmethod
    def _reply_guidance(packets: list[CognitivePacket], emoticon: EmoticonSelection) -> list[str]:
        lines: list[str] = []
        for packet in packets[:5]:
            if packet.response_guidance:
                lines.append(f"{packet.name}: {packet.response_guidance}")
            if packet.response_steps:
                lines.append(f"{packet.name} — kroki: " + "; ".join(packet.response_steps[:3]))
        lines.append(f"Dobór emotikonu: {emoticon.marker} — {emoticon.reason}. {emoticon.policy}")
        return lines
