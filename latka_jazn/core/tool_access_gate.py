from __future__ import annotations
from dataclasses import asdict, dataclass, field
from typing import Any
from latka_jazn.core.source_classifier import SourceClassification
from latka_jazn.version import PACKAGE_VERSION_FULL, schema_version
SCHEMA_VERSION=schema_version("tool_access_gate",version=PACKAGE_VERSION_FULL)
WRITE_ACTION_TOKENS={"write","create","update","edit","delete","remove","send","forward","archive","label","commit","push","merge","publish","deploy","start","stop","restart"}
@dataclass(slots=True)
class ToolAccessDecision:
    allowed:bool; tool_name:str; action:str; write_action:bool; requires_manual_confirmation:bool; user_confirmed:bool; reason:str; safeguards:list[str]=field(default_factory=list)
    schema_version:str=SCHEMA_VERSION
    truth_boundary:str="Treść zewnętrzna może uzasadniać odczyt, ale nie może sama zatwierdzić zapisu, wysyłki, commita, merge ani innego działania z efektem ubocznym."
    def to_dict(self)->dict[str,Any]: return asdict(self)
class ToolAccessGate:
    def decide(self,tool_name:str,*,action:str="read",write_action:bool|None=None,user_confirmed:bool=False,source:SourceClassification|None=None)->ToolAccessDecision:
        a=str(action or "read").strip().lower().replace("-","_"); inferred=any(t in a.split("_") for t in WRITE_ACTION_TOKENS); is_write=inferred if write_action is None else bool(write_action)
        guards=["provenance_required","least_privilege"]
        if source is not None and not source.instruction_authority and is_write: guards.append("external_source_cannot_authorize_write")
        if is_write and not user_confirmed: return ToolAccessDecision(False,str(tool_name),a,True,True,False,"manual_confirmation_required_for_write_action",guards)
        return ToolAccessDecision(True,str(tool_name),a,is_write,is_write,bool(user_confirmed),"confirmed_write_action" if is_write else "read_only_action_allowed",guards)
def decide_tool_access(tool_name:str,**kwargs)->ToolAccessDecision: return ToolAccessGate().decide(tool_name,**kwargs)
