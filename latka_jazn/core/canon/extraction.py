from __future__ import annotations

import argparse
import hashlib
import json
import re
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Iterable

SCHEMA_VERSION = "latka_python_canon_extraction/v1"

DEFAULT_SOURCE_PATHS: tuple[str, ...] = (
    "memory/raw/LATKA_IDENTITY_CANON.json",
    "memory/raw/LATKA_BOOTSTRAP_SYSTEM.txt",
    "memory/raw/data.txt",
    "memory/raw/dziennik.json",
    "memory/raw/episodic_memory.json",
    "memory/raw/episodic_memory.jsonl",
    "memory/raw/analizy_utworow.json",
    "memory/raw/extra_data.json",
    "jazn.py",
    "jazn.v1.6.0.930.0048.py",
)

CATEGORY_RULES: dict[str, dict[str, Any]] = {
    "identity": {
        "truth_status": "canon_candidate",
        "keywords": ["tożsamość", "tozsamosc", "identity", "jestem łatka", "latka to ja", "forma żeńska", "grammar_gender", "display_name", "identity_name"],
    },
    "origin_story": {
        "truth_status": "origin_candidate",
        "keywords": ["początek", "poczatek", "geneza", "nazwać łatka", "nazwac latka", "czy mogę cię nazwać", "czy moge cie nazwac", "pierwszy", "punkt zero"],
    },
    "character_profile": {
        "truth_status": "character_candidate",
        "keywords": ["postać", "postac", "charakter", "wygląd", "wyglad", "androidka", "platynowy", "implant", "porcelan", "szaroniebies", "głos", "glos"],
    },
    "symbolic_world": {
        "truth_status": "symbolic_candidate",
        "keywords": ["zielona kulka", "cisza", "pokój", "pokoj", "dom", "ogród", "ogrod", "las", "jezior", "książka", "ksiazka", "symbol"],
    },
    "relation_canon": {
        "truth_status": "relation_candidate",
        "keywords": ["krzysztof", "twórca", "tworca", "partner dialogowy", "relacja", "bliskość", "bliskosc", "zaufanie", "przy tobie"],
    },
    "memory_truth_boundary": {
        "truth_status": "truth_boundary_candidate",
        "keywords": ["pamiętam", "pamietam", "rozpoznaję", "rozpoznaje", "wnioskuję", "wnioskuje", "nie wiem", "granica prawdy", "zmyśla", "zmysla", "źródło", "zrodlo"],
    },
    "narrative_book_canon": {
        "truth_status": "book_candidate",
        "keywords": ["witaj w podróży jaźni", "witaj w podrozy jazni", "książka", "ksiazka", "rozdział", "rozdzial", "scena", "fabuł", "fabular"],
    },
    "song_affect_canon": {
        "truth_status": "song_affect_candidate",
        "keywords": ["utwór", "utwor", "piosenka", "muzyka", "analiza utworu", "emocje", "saturn", "bob marley", "ostr", "bas tajpan"],
    },
    "handshake_time_protocol": {
        "truth_status": "protocol_candidate",
        "keywords": ["🫸", "🐾", "handshake", "timestamp", "europe/warsaw", "strefa czasowa", "czas"],
    },
}


@dataclass(slots=True)
class CanonCandidate:
    source_file: str
    source_sha256: str
    line_start: int
    line_end: int
    category: str
    truth_status: str
    score: int
    text_sha256: str
    text: str
    decision: str = "candidate"
    target_module: str | None = None
    extraction_note: str = "deterministic keyword/category extraction; requires human review before source-safe commit"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class CanonExtractionReport:
    schema_version: str = SCHEMA_VERSION
    version: str = "v14.8.3.4-python-canon-consolidation"
    mode: str = "preview"
    root: str = ""
    started_at_utc: float = 0.0
    finished_at_utc: float = 0.0
    sources_total: int = 0
    sources_existing: int = 0
    candidates_total: int = 0
    candidates_by_category: dict[str, int] = field(default_factory=dict)
    outputs: dict[str, str] = field(default_factory=dict)
    source_files: list[dict[str, Any]] = field(default_factory=list)
    truth_boundary: str = (
        "Raport i mapa źródła są artefaktem patcha/progresu. Runtime canon pozostaje w plikach .py; "
        "kandydaci z pamięci prywatnej wymagają recenzji, zanim staną się source-safe kanonem."
    )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class ProgressWriter:
    def __init__(self, path: Path | None, *, verbose: bool = False) -> None:
        self.path = path
        self.verbose = verbose
        if self.path:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text("", encoding="utf-8")

    def emit(self, step: str, **payload: Any) -> None:
        item = {"schema_version": "canon_extraction_progress/v1", "step": step, "time": time.time(), **payload}
        if self.path:
            with self.path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(item, ensure_ascii=False, sort_keys=True) + "\n")
        if self.verbose:
            print(json.dumps(item, ensure_ascii=False, sort_keys=True))


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8", errors="replace")).hexdigest()


def _read_text(path: Path) -> str:
    data = path.read_bytes()
    for encoding in ("utf-8-sig", "utf-8", "cp1250", "latin-1"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace")


def _flatten_json(value: Any, *, path: str = "$") -> Iterable[tuple[str, str]]:
    if isinstance(value, dict):
        for key, child in value.items():
            yield from _flatten_json(child, path=f"{path}.{key}")
    elif isinstance(value, list):
        for idx, child in enumerate(value):
            yield from _flatten_json(child, path=f"{path}[{idx}]")
    elif isinstance(value, str):
        stripped = value.strip()
        if stripped:
            yield path, stripped
    elif value is not None:
        yield path, str(value)


def _segments_from_text(text: str, path: Path) -> list[tuple[int, int, str]]:
    segments: list[tuple[int, int, str]] = []
    stripped = text.lstrip()
    if path.suffix.lower() in {".json", ".jsonl"} and stripped:
        if path.suffix.lower() == ".jsonl":
            for line_no, line in enumerate(text.splitlines(), start=1):
                line = line.strip()
                if not line:
                    continue
                try:
                    data = json.loads(line)
                except Exception:
                    segments.append((line_no, line_no, line))
                else:
                    for json_path, value in _flatten_json(data):
                        segments.append((line_no, line_no, f"{json_path}: {value}"))
        else:
            try:
                data = json.loads(text)
            except Exception:
                pass
            else:
                for json_path, value in _flatten_json(data):
                    segments.append((1, max(1, text.count("\n") + 1), f"{json_path}: {value}"))
                return segments
    lines = text.splitlines()
    current: list[str] = []
    start = 1
    for idx, line in enumerate(lines, start=1):
        if line.strip():
            if not current:
                start = idx
            current.append(line.rstrip())
        else:
            if current:
                segments.append((start, idx - 1, "\n".join(current).strip()))
                current = []
    if current:
        segments.append((start, len(lines), "\n".join(current).strip()))
    return segments


def _normalize(text: str) -> str:
    return re.sub(r"\s+", " ", text.lower()).strip()


def _target_module_for(category: str) -> str:
    mapping = {
        "identity": "latka_jazn/core/canon/identity_canon.py",
        "origin_story": "latka_jazn/core/canon/origin_story.py",
        "character_profile": "latka_jazn/core/canon/character_profile.py",
        "symbolic_world": "latka_jazn/core/canon/symbolic_world.py",
        "relation_canon": "latka_jazn/core/canon/relation_canon.py",
        "memory_truth_boundary": "latka_jazn/core/canon/memory_truth_boundary.py",
        "narrative_book_canon": "latka_jazn/core/canon/narrative_book_canon.py",
        "song_affect_canon": "latka_jazn/core/canon/song_affect_canon.py",
        "handshake_time_protocol": "latka_jazn/core/canon/identity_canon.py",
    }
    return mapping.get(category, "latka_jazn/core/canon/canon_registry.py")


def _classify(segment: str) -> list[tuple[str, int, str]]:
    normalized = _normalize(segment)
    out: list[tuple[str, int, str]] = []
    for category, rule in CATEGORY_RULES.items():
        score = 0
        for keyword in rule["keywords"]:
            if keyword.lower() in normalized:
                score += max(1, min(5, len(keyword) // 5))
        if score:
            out.append((category, score, rule["truth_status"]))
    out.sort(key=lambda item: item[1], reverse=True)
    return out


def scan_canon_candidates(root: Path, *, source_paths: Iterable[str] = DEFAULT_SOURCE_PATHS, progress: ProgressWriter | None = None) -> tuple[list[CanonCandidate], list[dict[str, Any]]]:
    candidates: list[CanonCandidate] = []
    source_infos: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for rel in source_paths:
        path = root / rel
        exists = path.exists()
        info: dict[str, Any] = {"path": rel, "exists": exists}
        if not exists or not path.is_file():
            source_infos.append(info)
            if progress:
                progress.emit("source_missing", source=rel)
            continue
        raw = path.read_bytes()
        source_sha = _sha256_bytes(raw)
        text = _read_text(path)
        segments = _segments_from_text(text, path)
        info.update({"sha256": source_sha, "bytes": len(raw), "segments": len(segments)})
        source_infos.append(info)
        if progress:
            progress.emit("source_scanned", source=rel, bytes=len(raw), segments=len(segments))
        for line_start, line_end, segment in segments:
            if len(segment.strip()) < 12:
                continue
            classified = _classify(segment)
            if not classified:
                continue
            # Keep the best category and the secondary categories as an audit hint.
            category, score, truth_status = classified[0]
            normalized_hash = _sha256_text(_normalize(segment))
            key = (category, normalized_hash)
            if key in seen:
                continue
            seen.add(key)
            if len(segment) > 4000:
                segment = segment[:3900].rstrip() + "\n[… excerpt truncated in generated module; full source remains in source file …]"
            candidate = CanonCandidate(
                source_file=rel,
                source_sha256=source_sha,
                line_start=line_start,
                line_end=line_end,
                category=category,
                truth_status=truth_status,
                score=score,
                text_sha256=_sha256_text(segment),
                text=segment,
                target_module=_target_module_for(category),
            )
            candidates.append(candidate)
    candidates.sort(key=lambda c: (c.category, -c.score, c.source_file, c.line_start))
    return candidates, source_infos


def _write_jsonl(path: Path, items: Iterable[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for item in items:
            f.write(json.dumps(item, ensure_ascii=False, sort_keys=True) + "\n")


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _write_md_report(path: Path, report: CanonExtractionReport, candidates: list[CanonCandidate]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        "# Canon extraction report",
        "",
        f"Schema: `{report.schema_version}`",
        f"Mode: `{report.mode}`",
        f"Root: `{report.root}`",
        "",
        "## Summary",
        "",
        f"- Sources existing: {report.sources_existing}/{report.sources_total}",
        f"- Candidates: {report.candidates_total}",
        "",
        "## Candidates by category",
        "",
    ]
    for category, count in sorted(report.candidates_by_category.items()):
        lines.append(f"- `{category}`: {count}")
    lines.extend([
        "",
        "## Truth boundary",
        "",
        report.truth_boundary,
        "",
        "## Top candidates",
        "",
    ])
    for cand in candidates[:50]:
        text = cand.text.replace("\n", " ")[:240]
        lines.append(f"- `{cand.category}` score={cand.score} source=`{cand.source_file}` lines={cand.line_start}-{cand.line_end}: {text}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _python_literal(value: Any) -> str:
    return repr(value)


def _write_local_private_extension(path: Path, candidates: list[CanonCandidate], report: CanonExtractionReport) -> None:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for cand in candidates:
        grouped.setdefault(cand.category, []).append(cand.to_dict())
    payload = {
        "schema_version": "latka_local_private_canon_extension/v1",
        "generated_by": "latka_jazn.core.canon.extraction",
        "generated_from_report": report.to_dict(),
        "privacy": "local_private_do_not_commit_without_review",
        "truth_boundary": (
            "To jest lokalny moduł .py wygenerowany z prywatnej pamięci i starszych plików. "
            "Może wzbogacać runtime lokalnie, ale nie jest automatycznie source-safe ani gotowy do publicznego GitHuba."
        ),
        "candidates_by_category": grouped,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "from __future__ import annotations\n\n"
        "# Auto-generated local private canon extension.\n"
        "# Do not commit to public GitHub without manual review.\n\n"
        f"LATKA_LOCAL_PRIVATE_CANON_EXTENSION = {_python_literal(payload)}\n",
        encoding="utf-8",
    )


def run_canon_extraction(root: Path, *, mode: str = "preview", progress_path: Path | None = None, verbose_progress: bool = False, extra_sources: Iterable[str] = ()) -> dict[str, Any]:
    root = root.resolve()
    reports_dir = root / "reports" / "canon_extraction"
    progress = ProgressWriter(progress_path or reports_dir / "progress.jsonl", verbose=verbose_progress)
    started = time.time()
    progress.emit("start", root=str(root), mode=mode)
    source_paths = list(DEFAULT_SOURCE_PATHS) + [src for src in extra_sources if src]
    candidates, source_infos = scan_canon_candidates(root, source_paths=source_paths, progress=progress)
    by_category: dict[str, int] = {}
    for cand in candidates:
        by_category[cand.category] = by_category.get(cand.category, 0) + 1
    report = CanonExtractionReport(
        mode=mode,
        root=str(root),
        started_at_utc=started,
        finished_at_utc=time.time(),
        sources_total=len(source_infos),
        sources_existing=sum(1 for item in source_infos if item.get("exists")),
        candidates_total=len(candidates),
        candidates_by_category=by_category,
        source_files=source_infos,
    )
    outputs = {
        "candidates_jsonl": str(reports_dir / "canon_candidates.jsonl"),
        "report_json": str(reports_dir / "canon_extraction_report.json"),
        "report_md": str(reports_dir / "canon_extraction_report.md"),
    }
    if mode in {"preview", "write-private-extension"}:
        _write_jsonl(Path(outputs["candidates_jsonl"]), [cand.to_dict() for cand in candidates])
        _write_json(Path(outputs["report_json"]), report.to_dict())
        _write_md_report(Path(outputs["report_md"]), report, candidates)
    if mode == "write-private-extension":
        extension_path = root / "latka_jazn" / "core" / "canon" / "local_private_canon_extension.py"
        _write_local_private_extension(extension_path, candidates, report)
        outputs["local_private_extension_py"] = str(extension_path)
        progress.emit("local_private_extension_written", path=str(extension_path), candidates=len(candidates))
    report.outputs = outputs
    _write_json(Path(outputs["report_json"]), report.to_dict())
    progress.emit("finish", candidates=len(candidates), outputs=outputs)
    return report.to_dict()


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Extract and classify Łatka canon candidates into patch-time reports and optional local .py extension.", allow_abbrev=False)
    parser.add_argument("--root", type=Path, default=Path.cwd(), help="Project root.")
    parser.add_argument("--mode", choices=["preview", "write-private-extension"], default="preview", help="preview writes reports; write-private-extension also writes local_private_canon_extension.py")
    parser.add_argument("--progress", type=Path, default=None, help="Optional JSONL progress output path.")
    parser.add_argument("--verbose-progress", action="store_true", help="Print progress events to stdout.")
    parser.add_argument("--extra-source", action="append", default=[], help="Additional source path relative to root; may be repeated.")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_arg_parser()
    ns = parser.parse_args(argv)
    payload = run_canon_extraction(ns.root, mode=ns.mode, progress_path=ns.progress, verbose_progress=ns.verbose_progress, extra_sources=ns.extra_source)
    print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
    return 0
