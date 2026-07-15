from __future__ import annotations

from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any
import json


@dataclass(frozen=True, slots=True)
class GitHubRepositoryTarget:
    name: str
    purpose: str
    suggested_root: str
    include: tuple[str, ...]
    exclude: tuple[str, ...]
    commit_policy: str
    truth_rule: str

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["include"] = list(self.include)
        data["exclude"] = list(self.exclude)
        return data


@dataclass(frozen=True, slots=True)
class GitHubRepositoryPlan:
    schema_version: str
    repositories: tuple[GitHubRepositoryTarget, ...]
    files_to_add: tuple[str, ...]
    files_to_keep_private: tuple[str, ...]
    chatgpt_connector_note: str
    commit_strategy: str
    export_strategy: str
    truth_boundary: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "repositories": [repo.to_dict() for repo in self.repositories],
            "files_to_add": list(self.files_to_add),
            "files_to_keep_private": list(self.files_to_keep_private),
            "chatgpt_connector_note": self.chatgpt_connector_note,
            "commit_strategy": self.commit_strategy,
            "export_strategy": self.export_strategy,
            "truth_boundary": self.truth_boundary,
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=2, sort_keys=True)


def build_github_repository_plan(root: Path | str | None = None) -> GitHubRepositoryPlan:
    """Plan przygotowania repozytoriów bez wykonywania pushu.

    Funkcja nie korzysta z tokenów i nie zapisuje do GitHub. Tworzy kontrakt,
    który można zapisać w repo i użyć później przez ChatGPT/GitHub connector,
    lokalne git CLI albo GitHub Desktop.
    """

    return GitHubRepositoryPlan(
        schema_version="github_repository_plan/v1",
        repositories=(
            GitHubRepositoryTarget(
                name="SmuklyLew/Latka.Jazn",
                purpose="główne prywatne repo systemu Jaźni: kod, testy, dokumentacja, manifesty i bezpieczne zasoby źródłowe",
                suggested_root="/",
                include=(
                    "latka_jazn/", "tests/", "docs/", "tools/", "patches/", "reports/",
                    "README.md", "VERSION.txt", "START_CHATGPT_FROM_HERE.txt", "MANIFEST_*.json", "UPDATE_*.md",
                    "DOWNLOAD_SAFE_MANIFEST.json", ".gitignore", "GITHUB_REPOSITORY_PLAN.json",
                ),
                exclude=(
                    "memory/raw/chat.html", "memory/raw/chat.html.7z", "workspace_runtime/", "exports/", "*.sqlite3-wal", "*.sqlite3-shm",
                    "__pycache__/", ".pytest_cache/", "*.zip", "*.tmp",
                ),
                commit_policy="commit po sprawdzonej aktualizacji systemu, po testach i po wygenerowaniu raportu; nie commitować każdej drobnej tury rozmowy",
                truth_rule="to repo zawiera system, ale nie zastępuje repo pamięci i nie gwarantuje aktualnej rozmowy bez synchronizacji",
            ),
            GitHubRepositoryTarget(
                name="SmuklyLew/Latka.Jazn.Memory",
                purpose="prywatne repo pamięci tekstowej i checkpointów: dziennik, layered memory, session continuity, manifesty pamięci",
                suggested_root="/memory/",
                include=(
                    "memory/raw/dziennik.json", "memory/raw/conversation_turns.jsonl", "memory/raw/runtime_events.jsonl",
                    "memory/raw/session_continuity_index.json", "memory/layered/", "memory/exported_from_sqlite/",
                    "memory/RAW_MEMORY_MANIFEST.json", "memory/update_protocol.json", "MEMORY_CHECKPOINT_POLICY.md",
                ),
                exclude=(
                    "memory/raw/chat.html", "memory/raw/chat.html.7z", "workspace_runtime/*.sqlite3-wal", "workspace_runtime/*.sqlite3-shm",
                    "exports/", "*.zip", "*.tmp", "__pycache__/",
                ),
                commit_policy="checkpointy zbiorcze: po ważnej rozmowie, po dniu pracy, po aktualizacji albo przed zamknięciem dłuższej sesji; append-only bez przepisywania historii bez potrzeby",
                truth_rule="pamięć w repo jest źródłem trwałości dopiero po commicie/pushu; bieżący sandbox ChatGPT nie daje twardej gwarancji długiego utrzymania plików",
            ),
        ),
        files_to_add=(
            "GITHUB_REPOSITORY_PLAN.json",
            "docs/GITHUB_REPOSITORY_WORKFLOW.md",
            "MEMORY_CHECKPOINT_POLICY.md",
            ".gitignore",
        ),
        files_to_keep_private=(
            "memory/raw/chat.html",
            "memory/raw/chat.html.7z",
            "workspace_runtime/*.sqlite3",
            "*.env",
            "client_secret.json",
            "token.json",
        ),
        chatgpt_connector_note=(
            "Po podłączeniu prywatnych repozytoriów ChatGPT może używać ich jako źródeł kontekstu, ale zapis do GitHub wymaga osobnej akcji narzędziowej, lokalnego commita albo zatwierdzenia zmian w integracji. "
            "Nie zakładać, że sama rozmowa zapisała się w repo."
        ),
        commit_strategy=(
            "System i pamięć rozdzielać: Latka.Jazn dla kodu i dokumentacji; Latka.Jazn.Memory dla dziennika, ledgerów i warstw pamięci. "
            "Najpierw testy, potem manifest, potem commit; duże surowe archiwum chat.html.7z trzymać poza zwykłym commitem albo przez osobną decyzję/LFS."
        ),
        export_strategy=(
            "Eksport ZIP zostaje jako przenośna kopia pełna. Repozytoria są źródłem prawdy między sesjami; ZIP jest snapshotem do pobrania i awaryjnego odtworzenia."
        ),
        truth_boundary=(
            "Ten plan przygotowuje system do GitHub, ale nie wykonuje pushu. Repo staje się aktualne dopiero po realnym zapisie/commicie/pushu poza samą odpowiedzią runtime."
        ),
    )


def write_github_repository_plan(root: Path | str) -> Path:
    root = Path(root)
    path = root / "GITHUB_REPOSITORY_PLAN.json"
    path.write_text(build_github_repository_plan(root).to_json() + "\n", encoding="utf-8")
    return path
