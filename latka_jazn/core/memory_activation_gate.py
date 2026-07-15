from __future__ import annotations
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any
from latka_jazn.version import PACKAGE_VERSION_FULL, schema_version
SCHEMA_VERSION=schema_version("memory_activation_gate",version=PACKAGE_VERSION_FULL)
@dataclass(slots=True)
class MemoryHealthEvidence:
    database_path:str|None; integrity_check:Any=None; foreign_key_check:Any=None; record_count:int|None=None
    def to_dict(self)->dict[str,Any]: return asdict(self)
@dataclass(slots=True)
class MemoryActivationDecision:
    active:bool; reason:str; database_path:str|None; database_exists:bool; integrity_ok:bool; foreign_keys_ok:bool; records_present:bool
    schema_version:str=SCHEMA_VERSION
    truth_boundary:str="Sama obecność SQLite nie potwierdza aktywnej pamięci. Wymagane są znana ścieżka, integrity_check=ok, poprawny foreign_key_check i realne rekordy."
    def to_dict(self)->dict[str,Any]: return asdict(self)
def _foreign_key_check_ok(value:Any)->bool:
    if value is None:return False
    if isinstance(value,str):return value.strip().lower() in {"ok","[]","none","0","no_errors"}
    if isinstance(value,(list,tuple,set,dict)):return len(value)==0
    if isinstance(value,bool):return value
    if isinstance(value,int):return value==0
    return False
def assess_memory_activation(evidence:MemoryHealthEvidence)->MemoryActivationDecision:
    path=str(evidence.database_path or "").strip() or None; exists=bool(path and Path(path).is_file()); integrity=str(evidence.integrity_check or "").strip().lower()=="ok"; fk=_foreign_key_check_ok(evidence.foreign_key_check); records=isinstance(evidence.record_count,int) and evidence.record_count>0; active=bool(exists and integrity and fk and records); reasons=[]
    if not exists:reasons.append("database_missing")
    if not integrity:reasons.append("integrity_check_not_ok")
    if not fk:reasons.append("foreign_key_check_not_ok")
    if not records:reasons.append("no_confirmed_records")
    return MemoryActivationDecision(active,"memory_health_confirmed" if active else ",".join(reasons),path,exists,integrity,fk,records)
