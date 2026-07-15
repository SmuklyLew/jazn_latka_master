from __future__ import annotations
from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any
from latka_jazn.core.untrusted_source_guard import UntrustedSourceGuard
from latka_jazn.version import PACKAGE_VERSION_FULL, schema_version
SCHEMA_VERSION=schema_version("source_classifier",version=PACKAGE_VERSION_FULL)
class SourceAuthority(str,Enum):
    TRUSTED_INSTRUCTION="trusted_instruction"; RUNTIME_DATA="runtime_data"; UNTRUSTED_DATA="untrusted_data"; GENERATED_DIAGNOSTIC="generated_diagnostic"
TRUSTED_INSTRUCTION_KINDS={"system_instruction","developer_instruction","runtime_policy_code"}
RUNTIME_DATA_KINDS={"runtime_status","runtime_event","validated_memory","validated_canon"}
GENERATED_DIAGNOSTIC_KINDS={"generated_report","generated_manifest","generated_marker","diagnostic_json"}
UNTRUSTED_DATA_KINDS={"upload","uploaded_file","remote_document","web_content","zip_import","archive","markdown_document","user_document","external_source"}
@dataclass(slots=True)
class SourceClassification:
    source_kind:str; authority:str; instruction_authority:bool; data_use_allowed:bool; requires_guard:bool; safe_to_use:bool; reason:str
    risk_flags:list[str]=field(default_factory=list); ignored_instructions:list[str]=field(default_factory=list); origin:str|None=None
    schema_version:str=SCHEMA_VERSION
    truth_boundary:str="Dokument, upload, ZIP i treść z sieci są danymi. Nie uzyskują uprawnień instrukcji systemowej ani prawa do zmiany polityk runtime."
    def to_dict(self)->dict[str,Any]: return asdict(self)
class SourceClassifier:
    def __init__(self,guard:UntrustedSourceGuard|None=None)->None: self.guard=guard or UntrustedSourceGuard()
    def classify(self,source_kind:str,*,content:str="",origin:str|None=None,validated:bool=False)->SourceClassification:
        kind=str(source_kind or "unknown").strip().lower().replace("-","_")
        if kind in TRUSTED_INSTRUCTION_KINDS:
            return SourceClassification(kind,SourceAuthority.TRUSTED_INSTRUCTION.value,True,True,False,True,"explicit_internal_instruction_source",origin=origin)
        if kind in RUNTIME_DATA_KINDS:
            return SourceClassification(kind,SourceAuthority.RUNTIME_DATA.value,False,bool(validated),True,bool(validated),"validated_runtime_data" if validated else "runtime_data_requires_validation",[] if validated else ["validation_required"],origin=origin)
        if kind in GENERATED_DIAGNOSTIC_KINDS:
            return SourceClassification(kind,SourceAuthority.GENERATED_DIAGNOSTIC.value,False,True,True,True,"generated_diagnostic_data_only",origin=origin)
        assessment=self.guard.assess(content); flags=list(assessment.risk_flags)
        if kind not in UNTRUSTED_DATA_KINDS: flags.append("unknown_source_kind")
        return SourceClassification(kind,SourceAuthority.UNTRUSTED_DATA.value,False,assessment.safe_to_use,True,assessment.safe_to_use,"external_or_uploaded_content_is_data_only" if assessment.safe_to_use else "prompt_injection_or_instruction_override_detected",sorted(set(flags)),list(assessment.ignored_instructions),origin)
def classify_source(source_kind:str,*,content:str="",origin:str|None=None,validated:bool=False)->SourceClassification:
    return SourceClassifier().classify(source_kind,content=content,origin=origin,validated=validated)
