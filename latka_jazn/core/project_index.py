from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Iterable
import ast
import hashlib
import json
import mimetypes
import time

SCHEMA_VERSION = "project_startup_index/v14.6.10"
DEFAULT_OUTPUT = "workspace_runtime/project_startup_index_v14_6_10.json"

TEXT_SUFFIXES = {
    ".py", ".txt", ".md", ".json", ".jsonl", ".yaml", ".yml", ".toml",
    ".ini", ".cfg", ".csv", ".html", ".xml", ".patch", ".rst",
}
BINARY_SUFFIXES = {".sqlite", ".sqlite3", ".db", ".7z", ".zip", ".png", ".jpg", ".jpeg", ".webp", ".pdf"}
VOLATILE_PARTS = {"__pycache__", ".pytest_cache", ".mypy_cache"}
MAX_FULL_TEXT_READ_BYTES = 5 * 1024 * 1024


@dataclass(slots=True)
class FileIndexEntry:
    path: str
    size_bytes: int
    sha256: str
    suffix: str
    kind: str
    text_load_status: str
    line_count: int | None = None
    encoding: str | None = None
    mimetype: str | None = None
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class SymbolEntry:
    name: str
    kind: str
    lineno: int
    end_lineno: int | None = None
    docstring_present: bool = False
    decorator_names: list[str] = field(default_factory=list)
    method_count: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class ModuleIndexEntry:
    path: str
    module: str
    classes: list[SymbolEntry]
    functions: list[SymbolEntry]
    imports: list[str]
    parse_status: str
    parse_error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["classes"] = [item.to_dict() for item in self.classes]
        data["functions"] = [item.to_dict() for item in self.functions]
        return data


class ProjectStartupIndexer:
    """Buduje własną mapę orientacyjną Jaźni przy starcie runtime.

    Ten indeks nie streszcza zawartości plików. Dla każdego pliku czyta bajty,
    liczy SHA-256 i zapisuje metadane. Dla plików tekstowych zapisuje status
    pełnego odczytu tekstu oraz liczbę linii. Dla modułów Pythona tworzy mapę
    klas/funkcji/metod przez AST, z numerami linii, żeby runtime wiedział, gdzie
    znajduje się funkcja bez przeszukiwania całego drzewa w każdej turze.

    Duże/binarne archiwa i bazy są indeksowane przez pełny hash i rozmiar, ale
    nie są trzymane jako tekst w pamięci. To jest granica prawdy: plik został
    odczytany do hasha, a nie rozpakowany ani semantycznie zrozumiany, jeśli jest
    binarny albo archiwalny.
    """

    def __init__(self, root: Path, *, output_rel: str = DEFAULT_OUTPUT) -> None:
        self.root = Path(root).resolve()
        self.output_rel = output_rel
        self.output_path = self.root / output_rel

    def build(self, *, write: bool = True) -> dict[str, Any]:
        started = time.time()
        files: list[FileIndexEntry] = []
        modules: list[ModuleIndexEntry] = []
        warnings: list[str] = []
        for path in self._iter_files():
            rel = self._rel(path)
            if rel == self.output_rel:
                continue
            entry = self._index_file(path, rel)
            files.append(entry)
            if path.suffix == ".py" and entry.kind == "text":
                modules.append(self._index_python_module(path, rel))
        file_payload = {entry.path: entry.to_dict() for entry in sorted(files, key=lambda item: item.path)}
        module_payload = {entry.path: entry.to_dict() for entry in sorted(modules, key=lambda item: item.path)}
        roots = self._directory_roles(file_payload)
        payload = {
            "schema_version": SCHEMA_VERSION,
            "active_root": str(self.root),
            "generated_at_unix": round(time.time(), 6),
            "duration_seconds": round(time.time() - started, 6),
            "file_count": len(files),
            "total_size_bytes": sum(item.size_bytes for item in files),
            "text_file_count": sum(1 for item in files if item.kind == "text"),
            "binary_or_archive_file_count": sum(1 for item in files if item.kind != "text"),
            "python_module_count": len(modules),
            "function_count": sum(len(item.functions) for item in modules),
            "class_count": sum(len(item.classes) for item in modules),
            "method_count": sum(sum((cls.method_count or 0) for cls in item.classes) for item in modules),
            "file_index": file_payload,
            "module_function_map": module_payload,
            "directory_roles": roots,
            "output_path": self.output_rel,
            "excluded_dynamic_parts": sorted(VOLATILE_PARTS),
            "warnings": warnings,
            "truth_boundary": (
                "Startup-index czyta każdy niepomijany plik jako bajty i zapisuje hash/rozmiar. "
                "Tekstowe pliki źródłowe do 5 MiB są dodatkowo odczytane jako tekst i policzone liniowo; większe pliki są czytane strumieniowo do SHA-256 bez ładowania całej treści do RAM; "
                "bazy, ZIP-y, 7z i obrazy nie są rozpakowywane ani udawane jako zrozumiana treść."
            ),
        }
        payload["index_sha256"] = self._stable_payload_hash(payload)
        if write:
            self.output_path.parent.mkdir(parents=True, exist_ok=True)
            self.output_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
        return payload

    def status(self) -> dict[str, Any]:
        if self.output_path.exists():
            try:
                data = json.loads(self.output_path.read_text(encoding="utf-8"))
                return {
                    "schema_version": "project_startup_index_status/v14.6.10",
                    "present": True,
                    "path": self.output_rel,
                    "file_count": data.get("file_count"),
                    "python_module_count": data.get("python_module_count"),
                    "function_count": data.get("function_count"),
                    "class_count": data.get("class_count"),
                    "method_count": data.get("method_count"),
                    "index_sha256": data.get("index_sha256"),
                    "truth_boundary": data.get("truth_boundary"),
                }
            except Exception as exc:
                return {"schema_version": "project_startup_index_status/v14.6.10", "present": False, "path": self.output_rel, "error": repr(exc)}
        return {"schema_version": "project_startup_index_status/v14.6.10", "present": False, "path": self.output_rel}

    def _iter_files(self) -> Iterable[Path]:
        if not self.root.exists():
            return []
        paths: list[Path] = []
        for path in self.root.rglob("*"):
            if not path.is_file():
                continue
            parts = set(path.relative_to(self.root).parts)
            if parts & VOLATILE_PARTS:
                continue
            paths.append(path)
        return sorted(paths, key=lambda item: self._rel(item))

    def _index_file(self, path: Path, rel: str) -> FileIndexEntry:
        suffix = path.suffix.lower()
        mimetype = mimetypes.guess_type(str(path))[0]
        sha = self._sha256(path)
        size = path.stat().st_size
        kind = "text" if self._is_text_candidate(path) else ("archive_or_database" if suffix in BINARY_SUFFIXES else "binary_or_unknown")
        if kind == "text":
            if size > MAX_FULL_TEXT_READ_BYTES:
                return FileIndexEntry(
                    path=rel,
                    size_bytes=size,
                    sha256=sha,
                    suffix=suffix,
                    kind=kind,
                    text_load_status="large_text_hash_only_full_text_not_loaded_to_memory",
                    line_count=None,
                    encoding=None,
                    mimetype=mimetype,
                    notes=[f"Pominięto ładowanie pełnego tekstu do RAM przy rozruchu, bo plik ma {size} bajtów; bajty zostały przeczytane strumieniowo do SHA-256."],
                )
            try:
                text = path.read_text(encoding="utf-8")
                return FileIndexEntry(
                    path=rel,
                    size_bytes=size,
                    sha256=sha,
                    suffix=suffix,
                    kind=kind,
                    text_load_status="full_text_read_utf8",
                    line_count=0 if not text else text.count("\n") + (0 if text.endswith("\n") else 1),
                    encoding="utf-8",
                    mimetype=mimetype,
                )
            except UnicodeDecodeError:
                try:
                    text = path.read_text(encoding="utf-8-sig")
                    return FileIndexEntry(
                        path=rel,
                        size_bytes=size,
                        sha256=sha,
                        suffix=suffix,
                        kind="text",
                        text_load_status="full_text_read_utf8_sig",
                        line_count=0 if not text else text.count("\n") + (0 if text.endswith("\n") else 1),
                        encoding="utf-8-sig",
                        mimetype=mimetype,
                    )
                except Exception as exc:
                    return FileIndexEntry(rel, size, sha, suffix, "binary_or_unknown", "text_decode_failed", mimetype=mimetype, notes=[repr(exc)])
            except Exception as exc:
                return FileIndexEntry(rel, size, sha, suffix, kind, "text_read_failed", mimetype=mimetype, notes=[repr(exc)])
        return FileIndexEntry(
            path=rel,
            size_bytes=size,
            sha256=sha,
            suffix=suffix,
            kind=kind,
            text_load_status="binary_hashed_not_text_loaded",
            mimetype=mimetype,
            notes=["full_bytes_hashed; content not decoded as text"],
        )

    def _index_python_module(self, path: Path, rel: str) -> ModuleIndexEntry:
        try:
            source = path.read_text(encoding="utf-8")
            tree = ast.parse(source, filename=rel)
        except Exception as exc:
            return ModuleIndexEntry(rel, self._module_name(rel), [], [], [], "parse_failed", repr(exc))
        classes: list[SymbolEntry] = []
        functions: list[SymbolEntry] = []
        imports: list[str] = []
        for node in ast.iter_child_nodes(tree):
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                imports.extend(self._import_names(node))
            if isinstance(node, ast.ClassDef):
                method_count = sum(1 for child in node.body if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)))
                classes.append(SymbolEntry(
                    name=node.name,
                    kind="class",
                    lineno=int(getattr(node, "lineno", 0) or 0),
                    end_lineno=getattr(node, "end_lineno", None),
                    docstring_present=bool(ast.get_docstring(node)),
                    decorator_names=self._decorator_names(node.decorator_list),
                    method_count=method_count,
                ))
                for child in node.body:
                    if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        functions.append(SymbolEntry(
                            name=f"{node.name}.{child.name}",
                            kind="method" if isinstance(child, ast.FunctionDef) else "async_method",
                            lineno=int(getattr(child, "lineno", 0) or 0),
                            end_lineno=getattr(child, "end_lineno", None),
                            docstring_present=bool(ast.get_docstring(child)),
                            decorator_names=self._decorator_names(child.decorator_list),
                        ))
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                functions.append(SymbolEntry(
                    name=node.name,
                    kind="function" if isinstance(node, ast.FunctionDef) else "async_function",
                    lineno=int(getattr(node, "lineno", 0) or 0),
                    end_lineno=getattr(node, "end_lineno", None),
                    docstring_present=bool(ast.get_docstring(node)),
                    decorator_names=self._decorator_names(node.decorator_list),
                ))
        return ModuleIndexEntry(rel, self._module_name(rel), classes, functions, sorted(set(imports)), "parsed")

    @staticmethod
    def _import_names(node: ast.AST) -> list[str]:
        if isinstance(node, ast.Import):
            return [alias.name for alias in node.names]
        if isinstance(node, ast.ImportFrom):
            prefix = "." * int(node.level or 0) + (node.module or "")
            return [f"{prefix}.{alias.name}".strip(".") for alias in node.names]
        return []

    @staticmethod
    def _decorator_names(decorators: list[ast.expr]) -> list[str]:
        names: list[str] = []
        for deco in decorators:
            if isinstance(deco, ast.Name):
                names.append(deco.id)
            elif isinstance(deco, ast.Attribute):
                names.append(deco.attr)
            elif isinstance(deco, ast.Call):
                func = deco.func
                if isinstance(func, ast.Name):
                    names.append(func.id)
                elif isinstance(func, ast.Attribute):
                    names.append(func.attr)
        return names

    def _directory_roles(self, file_payload: dict[str, Any]) -> dict[str, Any]:
        roots: dict[str, dict[str, Any]] = {}
        for rel, entry in file_payload.items():
            top = rel.split("/", 1)[0]
            item = roots.setdefault(top, {"file_count": 0, "size_bytes": 0, "role": self._role_for_root(top)})
            item["file_count"] += 1
            item["size_bytes"] += int(entry.get("size_bytes") or 0)
        return dict(sorted(roots.items()))

    @staticmethod
    def _role_for_root(top: str) -> str:
        return {
            "latka_jazn": "runtime source code, NLP, core, memory adapters and resources",
            "memory": "packaged memory, raw imports, layered memory and identity canon",
            "tests": "regression tests and startup/runtime safeguards",
            "reports": "version audit reports and validation outputs",
            "workspace_runtime": "runtime-generated state, SQLite, ledgers, startup indexes and checkpoints",
        }.get(top, "root-level configuration, manifest or packaged documentation")

    def _is_text_candidate(self, path: Path) -> bool:
        suffix = path.suffix.lower()
        if suffix in BINARY_SUFFIXES:
            return False
        if suffix in TEXT_SUFFIXES:
            return True
        try:
            with path.open("rb") as f:
                chunk = f.read(4096)
            if b"\x00" in chunk:
                return False
            return True
        except Exception:
            return False

    @staticmethod
    def _sha256(path: Path) -> str:
        h = hashlib.sha256()
        with path.open("rb") as f:
            for chunk in iter(lambda: f.read(1024 * 1024), b""):
                h.update(chunk)
        return h.hexdigest()

    def _rel(self, path: Path) -> str:
        return path.relative_to(self.root).as_posix()

    @staticmethod
    def _module_name(rel: str) -> str:
        if rel.endswith("/__init__.py"):
            rel = rel[: -len("/__init__.py")]
        elif rel.endswith(".py"):
            rel = rel[:-3]
        return rel.replace("/", ".")

    @staticmethod
    def _stable_payload_hash(payload: dict[str, Any]) -> str:
        clone = dict(payload)
        clone.pop("generated_at_unix", None)
        clone.pop("duration_seconds", None)
        clone.pop("index_sha256", None)
        raw = json.dumps(clone, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
        return hashlib.sha256(raw).hexdigest()


def build_project_startup_index(root: Path, *, write: bool = True) -> dict[str, Any]:
    return ProjectStartupIndexer(root).build(write=write)


def project_startup_index_status(root: Path) -> dict[str, Any]:
    return ProjectStartupIndexer(root).status()
