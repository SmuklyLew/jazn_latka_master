from __future__ import annotations
from dataclasses import asdict, dataclass, field
import json
from pathlib import Path
from typing import Any, Mapping
from latka_jazn.core.memory_activation_gate import MemoryHealthEvidence, assess_memory_activation
from latka_jazn.core.source_classifier import SourceClassifier
from latka_jazn.core.package_integrity_manifest import package_integrity_manifest_status
from latka_jazn.version import PACKAGE_VERSION_FULL, schema_version
from latka_jazn.core.version_source import (
    read_runtime_version_from_version_py,
    read_version_checkpoint,
)
SCHEMA_VERSION=schema_version("runtime_activation_status",version=PACKAGE_VERSION_FULL)
REQUIRED_FILES=("VERSION.txt","latka_jazn/version.py","main.py","latka_jazn")
@dataclass(slots=True)
class RuntimeActivationStatus:
    ok:bool; active_state:str; active_root:str; version:str|None; start_file:str|None; folder_ready:dict[str,Any]; manifest:dict[str,Any]; marker:dict[str,Any]; daemon:dict[str,Any]; time:dict[str,Any]; memory:dict[str,Any]; model:dict[str,Any]; tools:dict[str,Any]; voice:dict[str,Any]; fail_closed_reason:str|None; errors:list[str]=field(default_factory=list)
    schema_version:str=SCHEMA_VERSION
    truth_boundary:str="Aktywacja rozdziela folder, manifest, marker, proces, endpoint, heartbeat, czas, pamięć, model, narzędzia i voice. Żadna pojedyncza warstwa nie dowodzi całości."
    def to_dict(self)->dict[str,Any]: return asdict(self)
class RuntimeActivationCascade:
    def __init__(self,root:Path,*,heartbeat_max_age_seconds:float=120.0)->None: self.root=Path(root).resolve(); self.heartbeat_max_age_seconds=float(heartbeat_max_age_seconds)
    @staticmethod
    def _read_json(path:Path)->dict[str,Any]|None:
        try:
            d=json.loads(path.read_text(encoding='utf-8')); return d if isinstance(d,dict) else None
        except Exception: return None
    def _folder_status(self):
        required={}; errors=[]
        for name in REQUIRED_FILES:
            p=self.root/name; exists=p.is_dir() if name=='latka_jazn' else p.is_file(); required[name]='present' if exists else 'missing'
            if not exists: errors.append(f'missing:{name}')
        return {'ok':not errors,'required_files':required},errors
    def _manifest_status(self):
        status=package_integrity_manifest_status(self.root)
        canonical=read_runtime_version_from_version_py(self.root)
        checkpoint=read_version_checkpoint(self.root)
        checkpoint_matches=bool(canonical and checkpoint==canonical)
        manifest_matches=bool(canonical and status.version==canonical)
        ok=bool(status.present and status.valid_json and checkpoint_matches and manifest_matches)
        payload={**status.to_dict(),'ok':ok,'canonical_version':canonical,'checkpoint_version':checkpoint,'matches_version_checkpoint':checkpoint_matches,'matches_version_txt':checkpoint_matches,'matches_version_py':manifest_matches,'runtime_start_blocking':False,'reason':'verified' if ok else 'missing_stale_or_invalid_nonblocking'}
        return payload,canonical or status.version
    def _marker_status(self,supplied:Mapping[str,Any]|None):
        marker=dict(supplied or {}); marker_path=None
        if not marker:
            for c in (self.root/'workspace_runtime'/'JAZN_ACTIVE_RUNTIME.json',self.root/'JAZN_ACTIVE_RUNTIME.json'):
                if c.exists(): marker_path=c; marker=self._read_json(c) or {}; break
        ar=str(marker.get('active_root') or marker.get('active_folder') or '').strip(); matches=bool(ar and Path(ar).resolve()==self.root)
        kind=str(marker.get('marker_source') or marker.get('source') or 'generated_marker'); source=SourceClassifier().classify(kind,validated=matches)
        trusted=bool(matches and marker); lifecycle=str(marker.get('marker_lifecycle_state') or ('trusted' if trusted else 'imported' if marker else 'missing'))
        return {'ok':trusted,'trusted':trusted,'lifecycle_state':lifecycle,'active_root':ar or None,'active_root_matches':matches,'path':str(marker_path) if marker_path else None,'source_classification':source.to_dict()}
    def _daemon_status(self,supplied:Mapping[str,Any]|None):
        d=dict(supplied or {}); nested=d.get('status') if isinstance(d.get('status'),dict) else {}; merged={**d,**nested}; pid=merged.get('pid') or merged.get('daemon_pid')
        try: pid_ok=int(pid)>0
        except (TypeError,ValueError): pid_ok=False
        endpoint=bool(merged.get('endpoint_ok') or merged.get('endpoint_reachable') or str(merged.get('endpoint_status') or '').lower() in {'ok','ready','active'})
        fresh=bool(merged.get('heartbeat_fresh')); age=merged.get('heartbeat_age_seconds')
        if age is not None:
            try: fresh=fresh and float(age)<=self.heartbeat_max_age_seconds
            except (TypeError,ValueError): fresh=False
        ok=bool(pid_ok and endpoint and fresh)
        return {'ok':ok,'pid':int(pid) if pid_ok else None,'pid_alive':bool(merged.get('pid_alive',pid_ok)),'endpoint_ok':endpoint,'heartbeat_fresh':fresh,'heartbeat_age_seconds':age,'background_claim_allowed':ok,'reason':'pid_endpoint_heartbeat_confirmed' if ok else 'pid_endpoint_heartbeat_required'}
    def evaluate(self,*,marker_status=None,daemon_status=None,time_status=None,memory_status=None,model_status=None,tool_status=None,voice_status=None)->RuntimeActivationStatus:
        folder,errors=self._folder_status(); manifest,version=self._manifest_status()
        marker=self._marker_status(marker_status)
        if not marker['ok']: errors.append('marker_not_trusted')
        daemon=self._daemon_status(daemon_status)
        if not daemon['ok']: errors.append('daemon_pid_endpoint_heartbeat_unconfirmed')
        tp=dict(time_status or {}); time={'trusted':bool(tp.get('trusted') or tp.get('timestamp_trusted')),'source':tp.get('source') or tp.get('timestamp_source'),'timestamp':tp.get('timestamp') or tp.get('timestamp_iso')}
        mp=dict(memory_status or {}); memory=assess_memory_activation(MemoryHealthEvidence(mp.get('database') or mp.get('database_path'),mp.get('integrity_check'),mp.get('foreign_key_check'),mp.get('record_count'))).to_dict()
        mod=dict(model_status or {}); selected=str(mod.get('selected_backend_adapter') or mod.get('selected_adapter') or 'null_model_adapter'); visible=str(mod.get('visible_channel_adapter') or 'unknown')
        model={'selected_backend_adapter':selected,'visible_channel_adapter':visible,'backend_ready':selected not in {'','null_model_adapter','none'},'host_channel_only':selected=='chatgpt_runtime_adapter' or visible in {'chatgpt_host','chatgpt_runtime_adapter'},'truth_boundary':'chatgpt_runtime_adapter oznacza kanał hosta, nie lokalny model Pythona.'}
        t=dict(tool_status or {}); tools={'tool_access_state':t.get('tool_access_state','host_only'),'provenance_required':bool(t.get('provenance_required',True)),'write_confirmation_required':bool(t.get('write_confirmation_required',True))}
        v=dict(voice_status or {}); voice={'voice_allowed':bool(v.get('voice_allowed')) and marker['ok'],'reason':v.get('reason') or ('runtime_confirmed' if marker['ok'] else 'runtime_not_confirmed')}
        required=bool(folder['ok'] and marker['ok'] and daemon['ok']); state='active_trusted' if required and time['trusted'] else 'active_degraded' if required else 'inactive'; fail=None if required else (errors[0] if errors else 'runtime_not_confirmed')
        return RuntimeActivationStatus(required,state,str(self.root),version,manifest.get('start_file') or ('main.py' if (self.root/'main.py').exists() else None),folder,manifest,marker,daemon,time,memory,model,tools,voice,fail,errors)
