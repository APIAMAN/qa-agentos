"""Provider-independent text embeddings.

``Embedder`` is the only interface the rest of the code uses. ``GatewayEmbedder`` calls the
LiteLLM gateway (whatever provider the ``EMBEDDING_GATEWAY_MODEL_KEY`` alias points to);
``HashingEmbedder`` is a deterministic, offline stand-in for tests and evals.

Every vector returned is L2-normalised, so cosine similarity is a dot product and vectors from
different calls are comparable. ``model_identity`` + ``dimensions`` are stored with each vector;
vectors are only ever compared with vectors of the same identity.
"""

from __future__ import annotations

import hashlib
import math
import re
import time
from dataclasses import dataclass
from typing import List, Optional, Protocol, Sequence

from qa_agentos.core.config import Settings
from qa_agentos.runtime.gateway.client import LiteLLMClient
from qa_agentos.runtime.gateway.errors import ModelRequestRejectedError, ModelResponseInvalidError

MAX_EMBED_INPUTS = 32
MAX_EMBED_CHARS = 8000


@dataclass(frozen=True)
class EmbeddingResult:
    vectors: List[List[float]]
    model_identity: str
    dimensions: int
    tokens_input: int
    latency_ms: int
    cost_usd: float


class Embedder(Protocol):
    model_identity: str
    dimensions: int
    gateway_model_key: str

    async def embed(self, texts: Sequence[str]) -> EmbeddingResult: ...

    async def aclose(self) -> None: ...


def validate_embedding_inputs(texts: Sequence[str]) -> List[str]:
    if not texts:
        raise ModelRequestRejectedError("Embedding request needs at least one input.")
    if len(texts) > MAX_EMBED_INPUTS:
        raise ModelRequestRejectedError(f"At most {MAX_EMBED_INPUTS} inputs per embedding request.")
    cleaned = []
    for text in texts:
        if not isinstance(text, str) or not text.strip():
            raise ModelRequestRejectedError("Embedding inputs must be non-empty strings.")
        cleaned.append(text[:MAX_EMBED_CHARS])
    return cleaned


def l2_normalize(vector: Sequence[float]) -> List[float]:
    norm = math.sqrt(sum(x * x for x in vector))
    if norm == 0:
        raise ModelResponseInvalidError("Embedding vector has zero length.", details={"reason": "zero_vector"})
    return [x / norm for x in vector]


def cosine(a: Sequence[float], b: Sequence[float]) -> float:
    """Cosine similarity of two L2-normalised vectors (plain dot product)."""
    return sum(x * y for x, y in zip(a, b, strict=True))


class GatewayEmbedder:
    """Embeddings through LiteLLM ``/v1/embeddings``."""

    def __init__(
        self,
        client: LiteLLMClient,
        *,
        gateway_model_key: str,
        model_identity: str,
        dimensions: int,
        request_dimensions: bool = False,
        input_type: Optional[str] = None,
        timeout_seconds: float = 15.0,
        cost_per_1m_tokens: float = 0.0,
    ) -> None:
        self.client = client
        self.gateway_model_key = gateway_model_key
        self.model_identity = model_identity
        self.dimensions = dimensions
        self.request_dimensions = request_dimensions
        self.input_type = input_type
        self.timeout_seconds = timeout_seconds
        self.cost_per_1m_tokens = cost_per_1m_tokens

    async def aclose(self) -> None:
        await self.client.aclose()

    async def embed(self, texts: Sequence[str]) -> EmbeddingResult:
        inputs = validate_embedding_inputs(texts)
        started = time.monotonic()
        vectors, tokens = await self.client.embeddings(
            self.gateway_model_key,
            inputs,
            dimensions=self.dimensions if self.request_dimensions else None,
            input_type=self.input_type,
            timeout_seconds=self.timeout_seconds,
        )
        if len(vectors[0]) != self.dimensions:
            # Never store vectors whose size disagrees with the configured model identity.
            raise ModelResponseInvalidError(
                "Embedding dimension does not match configuration.",
                details={"expected": self.dimensions, "received": len(vectors[0]), "model": self.model_identity},
            )
        tokens_input = tokens if tokens is not None else 0
        return EmbeddingResult(
            vectors=[l2_normalize(v) for v in vectors],
            model_identity=self.model_identity,
            dimensions=self.dimensions,
            tokens_input=tokens_input,
            latency_ms=int((time.monotonic() - started) * 1000),
            cost_usd=round(tokens_input * self.cost_per_1m_tokens / 1_000_000, 6),
        )


_TOKEN = re.compile(r"[a-z0-9_]+")


class HashingEmbedder:
    """Deterministic feature-hashing embedder (unigrams + bigrams). Offline; for tests and evals.

    Similar texts share tokens and so get high cosine similarity; it captures lexical, not
    semantic, similarity. Never used unless ``EMBEDDING_PROVIDER=fake``.
    """

    gateway_model_key = "fake-hashing"

    def __init__(self, dimensions: int = 256) -> None:
        self.dimensions = dimensions
        self.model_identity = f"fake/hashing-v1-{dimensions}"

    def _vector(self, text: str) -> List[float]:
        tokens = _TOKEN.findall(text.lower())
        features = tokens + [f"{a} {b}" for a, b in zip(tokens, tokens[1:], strict=False)]
        vec = [0.0] * self.dimensions
        for feat in features or ["<empty>"]:
            digest = hashlib.sha256(feat.encode("utf-8")).digest()
            idx = int.from_bytes(digest[:4], "big") % self.dimensions
            vec[idx] += 1.0 if digest[4] & 1 else -1.0
        if not any(vec):
            vec[0] = 1.0
        return l2_normalize(vec)

    async def aclose(self) -> None:
        return None

    async def embed(self, texts: Sequence[str]) -> EmbeddingResult:
        inputs = validate_embedding_inputs(texts)
        return EmbeddingResult(
            vectors=[self._vector(t) for t in inputs],
            model_identity=self.model_identity,
            dimensions=self.dimensions,
            tokens_input=0,
            latency_ms=0,
            cost_usd=0.0,
        )


def embedder_from_settings(settings: Settings, client: Optional[LiteLLMClient] = None) -> Optional[Embedder]:
    """The configured embedder, or ``None`` when embeddings are disabled."""
    if not settings.EMBEDDINGS_ENABLED:
        return None
    if settings.EMBEDDING_PROVIDER == "fake":
        return HashingEmbedder(settings.EMBEDDING_DIMENSIONS)
    if settings.EMBEDDING_PROVIDER != "gateway":
        raise ValueError(f"Unknown EMBEDDING_PROVIDER {settings.EMBEDDING_PROVIDER!r}")
    return GatewayEmbedder(
        client or LiteLLMClient.from_settings(settings),
        gateway_model_key=settings.EMBEDDING_GATEWAY_MODEL_KEY,
        model_identity=settings.EMBEDDING_MODEL_IDENTITY,
        dimensions=settings.EMBEDDING_DIMENSIONS,
        request_dimensions=settings.EMBEDDING_REQUEST_DIMENSIONS,
        input_type=settings.EMBEDDING_INPUT_TYPE,
        timeout_seconds=settings.EMBEDDING_TIMEOUT_SECONDS,
        cost_per_1m_tokens=settings.EMBEDDING_COST_PER_1M_TOKENS,
    )
