from __future__ import annotations

from dataclasses import dataclass
import hashlib
import math
import re
from typing import Callable, Protocol, Sequence

TOKEN_RE = re.compile(r"\w+", flags=re.UNICODE)


class EmbeddingProvider(Protocol):
    name: str
    networked: bool

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        ...


@dataclass(slots=True)
class DisabledEmbeddingProvider:
    name: str = "disabled"
    networked: bool = False

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        raise RuntimeError("embedding_provider_disabled")


@dataclass(slots=True)
class LocalHashEmbeddingProvider:
    dimensions: int = 256
    name: str = "local_hash"
    networked: bool = False

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        result: list[list[float]] = []
        for text in texts:
            vector = [0.0] * self.dimensions
            for token in TOKEN_RE.findall(str(text).casefold()):
                digest = hashlib.blake2b(token.encode("utf-8"), digest_size=16).digest()
                index = int.from_bytes(digest[:8], "big") % self.dimensions
                sign = -1.0 if digest[8] & 1 else 1.0
                vector[index] += sign
            norm = math.sqrt(sum(value * value for value in vector)) or 1.0
            result.append([value / norm for value in vector])
        return result


@dataclass(slots=True)
class OptInNetworkEmbeddingProvider:
    callback: Callable[[Sequence[str]], list[list[float]]]
    explicit_opt_in: bool = False
    name: str = "network_opt_in"
    networked: bool = True

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        if not self.explicit_opt_in:
            raise PermissionError("network_embedding_requires_explicit_opt_in")
        return self.callback(texts)
