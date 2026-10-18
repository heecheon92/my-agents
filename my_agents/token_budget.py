"""Model-aware, offline text estimates with an explicit conservative fallback."""

from __future__ import annotations

import hashlib
import os
import tempfile
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

# Match installed tiktoken's official cache artifacts. Never download during a request.
_CACHE_HASHES = {
    "cl100k_base": "223921b76ee99bde995b7ff738513eef100fb51d18c93597a113bcffe865b2a7",
    "o200k_base": "446a9538cb6c348e3516120d7c08b09f57c36495e2acfffe59a5bf8b0cfb1a2d",
}


@lru_cache(maxsize=8)
def _cached_encoding(name: str):
    import tiktoken

    expected = _CACHE_HASHES.get(name)
    if expected is None:
        return None
    cache = os.environ.get(
        "TIKTOKEN_CACHE_DIR",
        os.environ.get("DATA_GYM_CACHE_DIR", os.path.join(tempfile.gettempdir(), "data-gym-cache")),
    )
    if not cache:
        return None
    url = f"https://openaipublic.blob.core.windows.net/encodings/{name}.tiktoken"
    path = Path(cache) / hashlib.sha1(url.encode()).hexdigest()
    if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != expected:
        return None
    return tiktoken.get_encoding(name)


@dataclass(frozen=True)
class TextTokenCounter:
    model: str
    encoding_name: str | None
    encoding: Any = None

    @property
    def method(self) -> str:
        return "model_tokenizer" if self.encoding is not None else "utf8_byte_fallback"

    def count(self, text: str) -> int:
        if self.encoding is None:
            return len(text.encode("utf-8"))
        return len(self.encoding.encode(text, disallowed_special=()))


def text_token_counter(model: str) -> TextTokenCounter:
    """Do not guess a tokenizer family for an unrecognized model identifier."""
    try:
        import tiktoken

        name = tiktoken.model.encoding_name_for_model(model)
        encoding = _cached_encoding(name)
        return TextTokenCounter(model, name if encoding is not None else None, encoding)
    except KeyError, ImportError, OSError, ValueError:
        return TextTokenCounter(model, None)
