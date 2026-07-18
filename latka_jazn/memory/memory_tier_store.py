from __future__ import annotations

from latka_jazn.memory.memory_checkpoint_store import MemoryCheckpointStoreMixin
from latka_jazn.memory.memory_promotion_outbox_store import PromotionOutboxStoreMixin
from latka_jazn.memory.memory_tier_core_store import MemoryTierCoreStore
from latka_jazn.memory.memory_tier_support import WorkingMemoryBudget, WriteSummary


class MemoryTierStore(PromotionOutboxStoreMixin, MemoryCheckpointStoreMixin, MemoryTierCoreStore):
    """Canonical transactional L1/L2/L3 store with promotion ledger, outbox and checkpoints."""


__all__ = ["MemoryTierStore", "WorkingMemoryBudget", "WriteSummary"]
