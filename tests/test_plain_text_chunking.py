"""Plain text chunk counts scale with document size, not sentence count."""

import math

import pytest

from my_agents.knowledge.extraction import _chunk_document_text, _chunk_text
from my_agents.knowledge.models import DocumentModel


def _assert_exact_coverage(text, chunks):
    previous_end = 0
    for content, start, end in chunks:
        assert content == text[start:end]
        assert 0 < len(content) <= 1500
        assert start <= previous_end or not text[previous_end:start].strip()
        previous_end = max(previous_end, end)
    assert not text[previous_end:].strip()


def test_supported_large_korean_upload_does_not_create_one_chunk_per_sentence():
    content = "가나다라 마바사. " * 200000
    assert len(content.encode("utf-8")) == 4_800_000
    chunks = _chunk_text(content)
    assert len(chunks) <= math.ceil(len(content) / 1300)
    assert len(chunks) < 2000
    _assert_exact_coverage(content, chunks)


@pytest.mark.parametrize("separator", [" ", "\n\n", "\n\n\n\n", "\n\t\n"])
def test_short_sentences_and_paragraphs_pack_without_rewriting_whitespace(separator):
    content = "  \n" + separator.join(f"Fact {index}." for index in range(1000)) + "\n\n"
    chunks = _chunk_text(content)
    assert len(chunks) <= math.ceil(len(content) / 1300)
    _assert_exact_coverage(content, chunks)


def test_long_units_preserve_overlap_and_exact_offsets_with_wide_whitespace():
    content = "  " + "A" * 1800 + " " * 800 + "B" * 1800 + "\n"
    chunks = _chunk_text(content)
    _assert_exact_coverage(content, chunks)
    assert any(
        start < prior_end
        for (_, _, prior_end), (_, start, _) in zip(chunks, chunks[1:], strict=False)
    )


def test_plain_text_ingestion_uses_packed_chunks():
    content = "First paragraph.\n\nSecond paragraph."
    chunks = _chunk_document_text(DocumentModel(content=content, source_type="text"))
    assert [(chunk, start, end) for chunk, start, end, *_ in chunks] == [(content, 0, len(content))]
