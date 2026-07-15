from __future__ import annotations
from latka_jazn.nlp.dictionary_entry import SemanticRelations
class SemanticRelationAdapter:
    def related_terms(self, term: str, relation: str | None = None) -> SemanticRelations:
        return SemanticRelations(term=term, relation=relation, related_terms=[], source_name='not_configured', confidence=0.0)
