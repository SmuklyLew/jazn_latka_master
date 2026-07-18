from __future__ import annotations

from datetime import datetime, timezone
import hashlib

import pytest

from latka_jazn.memory.book_memory import BookArtifactStatus, decide_canon, new_roleplay_session, new_scene_version
from latka_jazn.memory.media_memory import ExperienceSource, MediaModality, ObservationBasis, SourceClaim, new_observation
from latka_jazn.memory.memory_tiers import SourceEvidence

NOW = datetime(2026, 7, 18, 2, 0, tzinfo=timezone.utc)


def evidence() -> SourceEvidence:
    return SourceEvidence(
        source_type="chat_export_archive", source_id="imp-1", source_sha256="a" * 64,
        conversation_id="c", node_ids=("n",),
    )


def test_roleplay_draft_and_canon_are_distinct() -> None:
    roleplay = new_roleplay_session(
        project_id="book", title="Próba sceny", participants=("Krzysztof", "Łatka"),
        evidence=(evidence(),), purpose="sprawdzenie dialogu", started_at_utc=NOW,
    )
    scene = new_scene_version(
        project_id="book", scene_id="s1", title="Scena", content="Tekst sceny",
        created_by="Krzysztof", evidence=(evidence(),),
        based_on_roleplay_id=roleplay.roleplay_id, created_at_utc=NOW,
    )
    assert scene.status is BookArtifactStatus.DRAFT
    with pytest.raises(ValueError, match="explicit user approval"):
        decide_canon(
            scene, approved=True, decided_by="runtime", reason="auto",
            explicit_user_approval=False, decided_at_utc=NOW,
        )
    decision, canonical = decide_canon(
        scene, approved=True, decided_by="Krzysztof", reason="zaakceptowana wersja",
        explicit_user_approval=True, decided_at_utc=NOW,
    )
    assert canonical.is_canonical
    assert canonical.canon_decision_id == decision.decision_id
    assert roleplay.symbolic is True


def test_actual_media_observation_requires_configured_adapter() -> None:
    source = ExperienceSource(
        "asset-1", MediaModality.AUDIO, "song.wav", None, "audio/wav", True, (evidence(),),
    )
    with pytest.raises(ValueError, match="configured adapter"):
        new_observation(
            source, basis=ObservationBasis.ACTUAL_AUDIO, content="melodia", confidence=0.8,
            adapter_configured=False, observed_at_utc=NOW,
        )
    observation = new_observation(
        source, basis=ObservationBasis.TRANSCRIPT, content="tekst piosenki", confidence=0.9,
        adapter_configured=False, observed_at_utc=NOW,
    )
    assert observation.basis is ObservationBasis.TRANSCRIPT


def test_document_claim_remains_source_claim_not_interpretation() -> None:
    text = "Autor twierdzi, że pamięć wymaga konsolidacji."
    claim = SourceClaim(
        "claim-1", "doc-1", "s. 12", "Autor", text,
        hashlib.sha256(text.encode()).hexdigest(), (evidence(),),
    )
    assert "nie uznaje" in claim.truth_boundary
