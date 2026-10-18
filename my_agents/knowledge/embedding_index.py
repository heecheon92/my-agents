"""Version embedding spaces explicitly; never infer provenance from vector dimensions."""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import asdict, dataclass
from typing import TYPE_CHECKING

from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session

from my_agents.knowledge.models import EmbeddingIndexModel

if TYPE_CHECKING:
    from my_agents.knowledge.embeddings import EmbeddingProvider


@dataclass(frozen=True)
class EmbeddingIndexIdentity:
    provider: str
    model: str
    dimensions: int
    encoding: str
    preprocessing: str
    version: int = 1

    @property
    def key(self) -> str:
        payload = json.dumps(asdict(self), sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(payload.encode()).hexdigest()


def valid_vector(vector: list[float]) -> bool:
    return bool(vector) and all(
        isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v) for v in vector
    )


def embedding_identity(provider: EmbeddingProvider, vector: list[float]) -> EmbeddingIndexIdentity:
    """Resolve actual dimensions only after a successful provider response.

    Encoding/preprocessing are explicit properties of the embedding boundary. The
    generic defaults support injected test providers; deployed adapters declare their
    own versions. Unknown/empty provider identities and inconsistent vectors fail closed.
    """
    if not valid_vector(vector):
        raise ValueError("Embedding vector must be nonempty and finite")
    name, model = getattr(provider, "provider", None), getattr(provider, "model", None)
    if not isinstance(name, str) or not name or not isinstance(model, str) or not model:
        raise ValueError("Embedding provider must declare its provider and model identity")
    configured = getattr(provider, "dimensions", None)
    if configured is not None and configured != len(vector):
        raise ValueError("Embedding response dimensions do not match configuration")
    identity = EmbeddingIndexIdentity(
        provider=name,
        model=model,
        dimensions=len(vector),
        encoding=getattr(provider, "encoding_identity", "provider-managed"),
        preprocessing=getattr(provider, "preprocessing_identity", "raw-text-v1"),
        version=getattr(provider, "index_version", 1),
    )
    if (
        any(
            not isinstance(v, str) or not v or len(v) > 240
            for v in (identity.provider, identity.model, identity.encoding, identity.preprocessing)
        )
        or not isinstance(identity.version, int)
        or identity.version < 1
    ):
        raise ValueError("Invalid embedding index identity")
    return identity


def register_embedding_index(db: Session, identity: EmbeddingIndexIdentity) -> str:
    """Idempotent concurrent registration inside the caller's ingestion transaction."""
    if db.get(EmbeddingIndexModel, identity.key) is None:
        insert = pg_insert if db.get_bind().dialect.name == "postgresql" else sqlite_insert
        db.execute(
            insert(EmbeddingIndexModel)
            .values(id=identity.key, **asdict(identity))
            .on_conflict_do_nothing(index_elements=[EmbeddingIndexModel.id])
        )
    return identity.key


def compatible_embedding(
    provider: EmbeddingProvider,
    query: list[float],
    stored: list[float],
    stored_index_id: str | None,
) -> bool:
    if not stored_index_id or not valid_vector(stored) or len(query) != len(stored):
        return False
    try:
        return stored_index_id == embedding_identity(provider, query).key
    except ValueError:
        return False
