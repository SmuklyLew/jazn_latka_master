from __future__ import annotations
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import hashlib, json
from typing import Any, Mapping
from latka_jazn.core.tool_access_gate import ToolAccessDecision
from latka_jazn.version import PACKAGE_VERSION_FULL, schema_version
SCHEMA_VERSION=schema_version("tool_call_provenance",version=PACKAGE_VERSION_FULL)
@dataclass(slots=True)
class ToolCallProvenance:
    tool_name:str; action:str; actor:str; reason:str; input_origin:str; gate_allowed:bool; write_action:bool; user_confirmed:bool; result_source:str|None; audit_event_id:str; created_at_utc:str
    schema_version:str=SCHEMA_VERSION
    truth_boundary:str="Provenance opisuje kto, dlaczego i przez jaką bramkę wywołał narzędzie. Nie zastępuje potwierdzenia użytkownika ani walidacji wyniku."
    def to_dict(self)->dict[str,Any]: return asdict(self)
    def validate(self)->tuple[bool,list[str]]:
        errors=[]
        for f in ("tool_name","action","actor","reason","input_origin","audit_event_id"):
            if not str(getattr(self,f,"") or "").strip(): errors.append(f"missing_{f}")
        if self.write_action and not self.user_confirmed: errors.append("write_action_without_user_confirmation")
        if not self.gate_allowed: errors.append("tool_call_not_allowed_by_gate")
        return not errors,errors
def _stable_event_id(payload:Mapping[str,Any])->str:
    raw=json.dumps(dict(payload),ensure_ascii=False,sort_keys=True,separators=(",",":")); return "tool-"+hashlib.sha256(raw.encode()).hexdigest()[:24]
def build_tool_call_provenance(*,tool_name:str,action:str,actor:str,reason:str,input_origin:str,gate:ToolAccessDecision,result_source:str|None=None,created_at_utc:str|None=None)->ToolCallProvenance:
    ts=created_at_utc or datetime.now(timezone.utc).isoformat(); base={"tool_name":tool_name,"action":action,"actor":actor,"reason":reason,"input_origin":input_origin,"gate_allowed":gate.allowed,"write_action":gate.write_action,"user_confirmed":gate.user_confirmed,"result_source":result_source,"created_at_utc":ts}
    return ToolCallProvenance(**base,audit_event_id=_stable_event_id(base))
