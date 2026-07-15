from __future__ import annotations

from dataclasses import dataclass, asdict
from pathlib import Path
import json

@dataclass(slots=True)
class PackageProfile:
    name: str
    includes: list[str]
    excludes: list[str]
    purpose: str

    def to_dict(self) -> dict:
        return asdict(self)

def load_package_profiles(root: Path) -> dict[str, PackageProfile]:
    path = Path(root) / "latka_jazn" / "resources" / "zip_package_profiles_v14_6_2.json"
    data = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    profiles: dict[str, PackageProfile] = {}
    for item in data.get("profiles", []):
        profile = PackageProfile(
            name=str(item["name"]),
            includes=list(item.get("includes") or []),
            excludes=list(item.get("excludes") or []),
            purpose=str(item.get("purpose") or ""),
        )
        profiles[profile.name] = profile
    return profiles
