from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any
from urllib.parse import quote_plus


@dataclass(slots=True)
class LookupPlan:
    source_id: str
    query: str
    url: str
    online_required: bool
    cache_policy: str
    license_note: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class PolishOnlineLookupPlanner:
    """Buduje bezpieczne plany lookupu; nie scrapuje masowo stron."""

    def wsjp(self, term: str) -> LookupPlan:
        q = quote_plus((term or "").strip())
        return LookupPlan("wsjp-pan", term, f"https://wsjp.pl/szukaj/podstawowe/wyniki?szukaj={q}", True, "metadata_or_user_requested_lookup_only", "WSJP PAN: używać jako źródła lookupu i cytowania, nie vendorować pełnej bazy bez zgody/licencji.")

    def nkjp(self, term: str) -> LookupPlan:
        q = quote_plus((term or "").strip())
        return LookupPlan("nkjp", term, f"https://nkjp.pl/poliqarp/nkjp300/query/{q}/", True, "concordance_link_only", "NKJP: używać jako korpusu odniesienia; nie mirrorować całego zasobu w repo.")

    def sjp(self, term: str) -> LookupPlan:
        q = quote_plus((term or "").strip())
        return LookupPlan("sjp-pl", term, f"https://sjp.pl/{q}", True, "reference_link_only", "SJP.PL deklaruje różne otwarte licencje zależnie od wersji; używać pomocniczo.")
