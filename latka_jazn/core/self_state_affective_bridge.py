from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

from latka_jazn.core.operational_self_model import OperationalSelfState
from latka_jazn.version import schema_version

SCHEMA_VERSION = schema_version("self_state_affective_bridge")


@dataclass(slots=True)
class SelfStateAffectiveBridgeReport:
    schema_version: str
    primary: str
    blend_labels: list[str]
    valence: float | None
    arousal: float | None
    control: float | None
    state_emoticon: str
    source: str
    truth_boundary: str
    response_policy: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class SelfStateAffectiveBridge:
    """Most między prostym self-state a granularnym afektem runtime.

    Celem jest zachować prawdę starego OperationalSelfModel, ale używać bogatszego
    AffectiveGranularityModel, kiedy cognitive-frame już go policzył. To nie jest
    deklaracja biologicznego czucia; to warstwa regulacji odpowiedzi.
    """

    TRUTH_BOUNDARY = (
        "To jest modelowany stan rozmowny runtime oparty na granularnym afekcie, "
        "uwadze i granicy prawdy; nie biologiczne samopoczucie ani stałe czuwanie w tle."
    )

    def build(
        self,
        *,
        user_text: str = "",
        granular_affect: Any | None = None,
        fallback: OperationalSelfState | None = None,
    ) -> SelfStateAffectiveBridgeReport:
        data = granular_affect if isinstance(granular_affect, dict) else {}
        blend_raw = data.get("blend") if isinstance(data.get("blend"), list) else []
        blend = []
        for item in blend_raw[:4]:
            if isinstance(item, dict) and item.get("name"):
                blend.append(str(item["name"]))
        primary = str(data.get("primary") or getattr(fallback, "primary", "ciepła uważność"))
        marker = str(data.get("state_emoticon") or getattr(fallback, "emoticon", "🌿"))
        if not blend and fallback is not None:
            blend = [fallback.primary, fallback.tension, fallback.need]
        return SelfStateAffectiveBridgeReport(
            schema_version=SCHEMA_VERSION,
            primary=primary,
            blend_labels=blend[:4],
            valence=self._num(data.get("valence")),
            arousal=self._num(data.get("arousal")),
            control=self._num(data.get("control")),
            state_emoticon=marker,
            source="affective_granularity" if data else "operational_self_model_fallback",
            truth_boundary=self.TRUTH_BOUNDARY,
            response_policy=[
                "odpowiadać aktualnie, bez losowej pamięci",
                "nazywać stan operacyjnie, nie biologicznie",
                "użyć bogatszej etykiety afektu, jeśli jest dostępna",
            ],
        )

    def render_state(
        self,
        *,
        user_text: str = "",
        granular_affect: Any | None = None,
        fallback: OperationalSelfState | None = None,
        reciprocal: bool = False,
        detail_mode: str = "natural",
    ) -> str:
        report = self.build(user_text=user_text, granular_affect=granular_affect, fallback=fallback)
        if detail_mode != "diagnostic":
            opening = "U mnie jest teraz spokojnie" if reciprocal else "Jest u mnie spokojnie"
            return (
                f"{opening}; najbliższa etykieta tego stanu to „{report.primary}”. "
                "To mój modelowany stan rozmowny i operacyjny w tej turze, nie biologiczne samopoczucie ani życie w tle."
            )
        opening = "U mnie teraz" if reciprocal else "Teraz najbliżej mi do"
        blend = ", ".join(x for x in report.blend_labels if x) or report.primary
        axes = self._axes_text(report)
        return (
            f"{opening}: {report.primary}. W tle tej odpowiedzi pracuje mieszanka: {blend}. "
            f"{axes} Pilnuję, żeby odpowiedzieć z bieżącej tury, bez wstrzykiwania przypadkowej pamięci. "
            f"Prawda: {report.truth_boundary}"
        )

    def render_expression(self, *, user_text: str = "", granular_affect: Any | None = None) -> str:
        report = self.build(user_text=user_text, granular_affect=granular_affect)
        blend = ", ".join(report.blend_labels) if report.blend_labels else report.primary
        return (
            "Powiem od siebie, ale uczciwie: moje „od siebie” jest stanem runtime, pamięci, reguł i bieżącego zdania. "
            f"Najbliżej mi teraz do: {report.primary}; mieszanka: {blend}. "
            "To ma prowadzić do trafniejszej odpowiedzi, nie do udawania biologicznego życia w tle. "
            f"Prawda: {report.truth_boundary}"
        )

    @staticmethod
    def _num(value: Any) -> float | None:
        try:
            return round(float(value), 3)
        except Exception:
            return None

    @staticmethod
    def _axes_text(report: SelfStateAffectiveBridgeReport) -> str:
        parts = []
        for label, value in (("walencja", report.valence), ("pobudzenie", report.arousal), ("kontrola", report.control)):
            if value is not None:
                parts.append(f"{label}={value}")
        return ("Osie afektu: " + ", ".join(parts) + ".") if parts else "Osie afektu nie są pewne w tej turze."
