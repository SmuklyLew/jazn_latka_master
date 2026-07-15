from __future__ import annotations
from pathlib import Path
from typing import Any
from datetime import datetime, timezone
import hashlib, json, re

from latka_jazn.core.version_source import read_runtime_version_from_version_py

SCHEMA_VERSION='jazn_update_history_index/v14.6.10'

def _sha(p: Path) -> str:
    h=hashlib.sha256()
    with p.open('rb') as f:
        for c in iter(lambda:f.read(1024*1024), b''):
            h.update(c)
    return h.hexdigest()

def collect_manifest_files(root: Path) -> list[Path]:
    root=Path(root)
    hist=root/'docs'/'update_history'/'manifests'
    files=[]
    if hist.exists():
        files.extend(sorted(hist.glob('MANIFEST*.json')))
    files.extend(sorted([p for p in root.glob('MANIFEST*.json') if p.name != 'MANIFEST_CURRENT.json']))
    return sorted(set(files))

def parse_manifest(path: Path) -> tuple[dict[str,Any], bool, str|None]:
    try:
        return json.loads(path.read_text(encoding='utf-8')), True, None
    except Exception as exc:
        return {}, False, repr(exc)

def _listify(v):
    if isinstance(v, list): return v
    if isinstance(v, dict): return list(v.values())
    if isinstance(v, str): return [v]
    return []

def extract_declared_features(data: dict[str,Any]) -> list[str]:
    keys=['declared_features','features','changes','modules','capabilities','implemented_features']
    out=[]
    for k in keys:
        for item in _listify(data.get(k)):
            if isinstance(item, dict):
                out.append(str(item.get('name') or item.get('title') or item.get('module') or item)[:200])
            else:
                out.append(str(item)[:200])
    return sorted(set([x for x in out if x and x!='None']))

def extract_declared_files(data: dict[str,Any]) -> list[str]:
    out=[]
    for k in ['changed_files','added_files','files','target_files','new_files']:
        for item in _listify(data.get(k)):
            if isinstance(item, dict):
                out.append(str(item.get('path') or item.get('file') or item.get('name') or ''))
            else:
                out.append(str(item))
    return sorted(set([x for x in out if x]))

def extract_declared_tests(data: dict[str,Any]) -> list[str]:
    out=[]
    for item in _listify(data.get('tests')):
        if isinstance(item, dict):
            out.append(str(item.get('path') or item.get('name') or item))
        else:
            out.append(str(item))
    return sorted(set([x for x in out if x]))

def find_code_evidence(root: Path, feature: str) -> list[str]:
    tokens=[t.lower() for t in re.findall(r'[A-Za-z_]{5,}|[ąćęłńóśźżA-ZĄĆĘŁŃÓŚŹŻa-z]{5,}', feature or '')[:5]]
    if not tokens: return []
    hits=[]
    for base in [root/'latka_jazn', root/'main.py']:
        paths=[base] if base.is_file() else list(base.rglob('*.py')) if base.exists() else []
        for p in paths:
            try: s=p.read_text(encoding='utf-8', errors='ignore').lower()
            except Exception: continue
            if any(t in s for t in tokens):
                hits.append(p.relative_to(root).as_posix())
                if len(hits)>=8: return hits
    return hits

def find_test_evidence(root: Path, feature: str) -> list[str]:
    tokens=[t.lower() for t in re.findall(r'[A-Za-z_]{5,}|[ąćęłńóśźżA-ZĄĆĘŁŃÓŚŹŻa-z]{5,}', feature or '')[:5]]
    hits=[]
    for p in (root/'tests').rglob('test*.py') if (root/'tests').exists() else []:
        try: s=p.read_text(encoding='utf-8', errors='ignore').lower()
        except Exception: continue
        if any(t in s for t in tokens):
            hits.append(p.relative_to(root).as_posix())
    return hits[:8]

def classify_implementation_status(root: Path, feature: str) -> str:
    code=find_code_evidence(root, feature); tests=find_test_evidence(root, feature)
    if code and tests: return 'implemented'
    if code: return 'implemented_no_test'
    if not feature or len(feature)<8: return 'ambiguous'
    return 'declared_missing_code'

def write_index_json(root: Path) -> Path:
    root = Path(root)
    out = root / 'docs' / 'update_history' / 'INDEX.json'
    out.parent.mkdir(parents=True, exist_ok=True)

    # Preserve curated historical release entries.  Only entries generated from
    # archived MANIFEST*.json files are rebuilt here; version refresh must never
    # erase changelog/release history merely because no historical manifests are
    # present in a reduced runtime export.
    existing: dict[str, Any] = {}
    if out.is_file():
        try:
            loaded = json.loads(out.read_text(encoding='utf-8-sig'))
            if isinstance(loaded, dict):
                existing = loaded
        except Exception:
            existing = {}
    preserved_entries = [
        item
        for item in existing.get('entries', [])
        if isinstance(item, dict) and item.get('kind') != 'update_manifest'
    ]

    manifest_entries = []
    for p in collect_manifest_files(root):
        data, ok, err = parse_manifest(p)
        features = extract_declared_features(data) if ok else []
        files = extract_declared_files(data) if ok else []
        tests = extract_declared_tests(data) if ok else []
        statuses = [classify_implementation_status(root, f) for f in features[:20]]
        if 'declared_missing_code' in statuses:
            audit = 'partial'
        elif any(s in statuses for s in ['implemented', 'implemented_no_test']):
            audit = 'ok' if 'implemented_no_test' not in statuses else 'partial'
        else:
            audit = 'unchecked'
        manifest_entries.append({
            'path': p.relative_to(root).as_posix(),
            'filename': p.name,
            'declared_version': data.get('version') or data.get('runtime_version') or data.get('target_version'),
            'filename_version_hint': p.stem,
            'schema_version': data.get('schema_version'),
            'kind': 'update_manifest',
            'status': 'historical',
            'parse_ok': ok,
            'parse_error': err,
            'sha256': _sha(p),
            'declared_features': features,
            'declared_files': files,
            'declared_tests': tests,
            'implementation_audit_status': audit,
            'notes': [],
        })

    entries = preserved_entries + manifest_entries
    payload = {
        'schema_version': existing.get('schema_version') or SCHEMA_VERSION,
        'generated_at_utc': datetime.now(timezone.utc).isoformat(),
        'active_version': read_runtime_version_from_version_py(root, fallback='unknown') or 'unknown',
        'entries': entries,
        'entry_count': len(entries),
        'truth_boundary': existing.get('truth_boundary') or 'Indeks porządkuje historyczne manifesty. Nie oznacza, że wszystkie stare deklaracje muszą być aktywną funkcją runtime.',
    }
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    return out

def write_audit_report_md(root: Path) -> Path:
    root=Path(root)
    idx=json.loads((root/'docs'/'update_history'/'INDEX.json').read_text(encoding='utf-8')) if (root/'docs'/'update_history'/'INDEX.json').exists() else {}
    out=root/'reports'/'UPDATE_HISTORY_AUDIT_V14_6_10.md'; out.parent.mkdir(exist_ok=True)
    lines=['# Audyt historii manifestów v14.6.10','',f"Wpisy: {idx.get('entry_count',0)}",'', 'Statusy audytu:']
    counts={}
    for e in idx.get('entries',[]):
        counts[e.get('implementation_audit_status','unknown')]=counts.get(e.get('implementation_audit_status','unknown'),0)+1
    for k,v in sorted(counts.items()):
        lines.append(f'- {k}: {v}')
    out.write_text('\n'.join(lines)+'\n', encoding='utf-8')
    return out
