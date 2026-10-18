"""Offline document processing, provenance, and atomic replacement contracts."""

from __future__ import annotations

import hashlib
import json

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from my_agents.auth.models import UserModel
from my_agents.knowledge.document_processing import (
    SECTION_MAX_CHARS,
    CleanedSection,
    DocumentProcessingError,
    process_document_text,
)
from my_agents.knowledge.embeddings import deterministic_embedding
from my_agents.knowledge.extraction import (
    ExtractionAlreadyRunning,
    KnowledgeExtractionService,
    _chunk_document_text,
)
from my_agents.knowledge.models import (
    DocumentChunkModel,
    DocumentMetadataProfileModel,
    DocumentModel,
    DocumentParseArtifactModel,
    ExtractionRunModel,
    KnowledgeBaseModel,
)
from my_agents.knowledge.pdf_uploads import PDF_PAGE_SEPARATOR
from my_agents.persistence.database import Base
from my_agents.persistence.models import import_all_models
from my_agents.settings import Settings


class FakeChat:
    def __init__(self, transform=None):
        self.inputs = []
        self.transform = transform or (lambda text: text)

    def with_structured_output(self, schema, *, method):
        assert schema is CleanedSection
        assert method == "function_calling"
        return self

    def invoke(self, messages):
        assert "untrusted" in messages[0].content
        text = messages[1].content
        self.inputs.append(text)
        return {"markdown": self.transform(text)}


def _openai_settings(**overrides):
    return Settings(_env_file=None).model_copy(update={"response_mode": "openai", **overrides})


def test_standard_processing_preserves_text_and_parser_mapping_without_a_model(monkeypatch):
    monkeypatch.setattr(
        "my_agents.knowledge.document_processing.ChatOpenAI",
        lambda **_: pytest.fail("Standard mode must not construct a model"),
    )
    original = "# State\n\nmessages + add_messages\n"
    locations = '[{"markdown_start":0,"markdown_end":7,"source_location":{"page":1}}]'
    result = process_document_text(
        original, mode="standard", source_type="markdown", elements_json=locations
    )
    assert result.content == original
    assert result.elements_json == locations
    assert result.provenance.provider == "parser"
    assert result.provenance.input_sha256 == result.provenance.output_sha256


def test_deterministic_ai_mode_discloses_no_model_call(monkeypatch):
    monkeypatch.setattr(
        "my_agents.knowledge.document_processing.ChatOpenAI",
        lambda **_: pytest.fail("Deterministic mode must not construct a model"),
    )
    result = process_document_text("Original text", mode="ai_assisted", source_type="text")
    assert result.content == "Original text"
    assert result.provenance.mode == "ai_assisted"
    assert result.provenance.provider == "deterministic"
    assert result.provenance.warnings == ["ai_processing_skipped_in_deterministic_mode"]


def test_ai_processing_is_bounded_per_page_and_maps_original_spans():
    original = PDF_PAGE_SEPARATOR.join(["State   design\nSave values.", "Second   page."])
    chat = FakeChat(lambda text: text.replace("   ", " "))
    result = process_document_text(
        original,
        mode="ai_assisted",
        source_type="pdf",
        settings=_openai_settings(),
        chat_model=chat,
    )
    assert chat.inputs == ["State   design\nSave values.", "Second   page."]
    assert result.content == original.replace("   ", " ")
    locations = json.loads(result.elements_json)
    assert [item["source_location"]["page"] for item in locations] == [1, 2]
    for item in locations:
        source = item["source_location"]
        assert source["mapping"] == "section"
        assert (
            original[source["original_text_start"] : source["original_text_end"]].replace(
                "   ", " "
            )
            == result.content[item["markdown_start"] : item["markdown_end"]]
        )
    assert result.provenance.model == "gpt-6-luna"
    assert result.provenance.provider == "openai"
    assert result.provenance.output_sha256 == hashlib.sha256(result.content.encode()).hexdigest()


def test_short_sections_are_batched_without_losing_headings_or_tail():
    original = "\n\n".join(f"## Section {index}\n\nValue {index}." for index in range(500))
    chat = FakeChat()
    result = process_document_text(
        original,
        mode="ai_assisted",
        source_type="markdown",
        settings=_openai_settings(),
        chat_model=chat,
    )
    assert result.content == original
    assert 1 < len(chat.inputs) < 10
    assert all(len(item) <= SECTION_MAX_CHARS for item in chat.inputs)
    assert "Value 499." in chat.inputs[-1]


@pytest.mark.parametrize(
    ("source", "replacement"),
    [
        ("Keep all four facts 1, 2, 3, 4.", "Keep facts 1, 2, 3."),
        ("Never save temporary values.", "Save temporary values."),
        ("Rate is 3.5%.", "Rate is 35%."),
        ("Use C#.", "Use C."),
        ("Use messages + add_messages.", "Use `messages` + `add_messages`."),
        ("Original information.", "Original information. https://invented.example"),
    ],
)
def test_ai_processing_rejects_changed_facts_and_references(source, replacement):
    with pytest.raises(DocumentProcessingError, match="document_processing_fidelity_failed"):
        process_document_text(
            source,
            mode="ai_assisted",
            source_type="text",
            settings=_openai_settings(),
            chat_model=FakeChat(lambda _: replacement),
        )


def test_model_cannot_supply_invented_source_locations():
    class InventedLocationChat(FakeChat):
        def invoke(self, messages):
            return {"markdown": "Text", "source_location": {"page": 99}}

    with pytest.raises(DocumentProcessingError, match="document_processing_invalid_output"):
        process_document_text(
            "Text",
            mode="ai_assisted",
            source_type="text",
            settings=_openai_settings(),
            chat_model=InventedLocationChat(),
        )


@pytest.mark.parametrize("indent", ["    ", "\t"])
def test_ai_cannot_turn_first_indented_code_line_into_a_heading_or_comment(indent):
    original = indent + "x = 1\n"
    chat = FakeChat(lambda text: "# " + text)
    unchanged = process_document_text(
        original,
        mode="ai_assisted",
        source_type="markdown",
        settings=_openai_settings(),
        chat_model=chat,
    )
    assert unchanged.content == original
    assert chat.inputs == []
    assert unchanged.provenance.provider == "deterministic"
    assert "ai_processing_skipped_no_prose" in unchanged.provenance.warnings


def test_indented_code_remains_protected_after_request_boundary_splits_the_line():
    original = "    " + "x = 1; " * 1400 + "\n"
    chat = FakeChat(lambda text: "# " + text)
    result = process_document_text(
        original,
        mode="ai_assisted",
        source_type="markdown",
        settings=_openai_settings(),
        chat_model=chat,
    )
    assert result.content == original
    assert chat.inputs == []


@pytest.mark.parametrize(
    "protected",
    [
        "```python\nx = 1\n```\n",
        "````python\nx = 1\n````\n",
        "~~~python\nx = 1\n~~~\n",
        "| Name | Value |\n| One | 1 |\n",
        "    x = 1\n    y = 2\n",
        "```python\n" + "x = 1\n" * 1400 + "```\n",
    ],
)
def test_ai_formats_prose_while_protected_code_and_tables_never_reach_the_model(protected):
    original = "First prose\n\n" + protected + "\nLast prose\n"
    chat = FakeChat(lambda text: "# " + text)
    result = process_document_text(
        original,
        mode="ai_assisted",
        source_type="markdown",
        settings=_openai_settings(),
        chat_model=chat,
    )
    assert chat.inputs == ["First prose", "Last prose"]
    assert result.content == "# First prose\n\n" + protected + "\n# Last prose\n"
    assert result.provenance.provider == "openai"
    assert "protected_content_preserved_verbatim" in result.provenance.warnings
    elements = json.loads(result.elements_json)
    code_location = elements[1]["source_location"]
    assert (
        original[code_location["original_text_start"] : code_location["original_text_end"]]
        == protected
    )
    assert result.content[elements[1]["markdown_start"] : elements[1]["markdown_end"]] == protected


def test_live_korean_fixture_can_gain_a_heading_without_rewriting_indented_code():
    prose = (
        "State 설계 원칙\n\n"
        "다운스트림 노드, 감사, 복구에 필요한 값만 저장합니다.\n"
        "한 노드 안에서 다시 계산 가능한 임시값은 저장하지 않습니다.\n"
        "대화형 상태에는 messages + add_messages를 사용합니다.\n"
        "라우팅에는 next_step처럼 명시적인 필드를 둡니다."
    )
    code = '    def next_step():\n        return "done"\n'
    original = prose + "\n\n" + code
    chat = FakeChat(lambda text: "# " + text.replace('return "done"', 'return "changed"'))
    result = process_document_text(
        original,
        mode="ai_assisted",
        source_type="markdown",
        settings=_openai_settings(),
        chat_model=chat,
    )
    assert chat.inputs == [prose]
    assert result.content == "# " + original
    assert result.provenance.provider == "openai"


def test_fenced_code_across_pdf_pages_stays_outside_the_model():
    first = "First prose\n\n```python\nx = 1"
    second = 'print("done")\n```\n\nSecond prose'
    original = PDF_PAGE_SEPARATOR.join([first, second])
    chat = FakeChat(lambda text: "# " + text)
    result = process_document_text(
        original,
        mode="ai_assisted",
        source_type="pdf",
        settings=_openai_settings(),
        chat_model=chat,
    )
    assert chat.inputs == ["First prose", "Second prose"]
    assert result.content == "# " + original.replace("Second prose", "# Second prose")
    assert [element["source_location"]["page"] for element in json.loads(result.elements_json)] == [
        1,
        1,
        2,
        2,
    ]


def test_unclosed_fence_is_preserved_to_end_without_model_construction(monkeypatch):
    original = "````python\nx = 1\n```\nStill inside the four-backtick fence.\n"
    monkeypatch.setattr(
        "my_agents.knowledge.document_processing.ChatOpenAI",
        lambda **_: pytest.fail("Protected-only text must not construct a model"),
    )
    result = process_document_text(
        original,
        mode="ai_assisted",
        source_type="markdown",
        settings=_openai_settings(),
    )
    assert result.content == original
    assert result.provenance.provider == "deterministic"
    assert result.provenance.warnings == [
        "protected_content_preserved_verbatim",
        "ai_processing_skipped_no_prose",
    ]


def test_multi_block_location_does_not_fabricate_one_exact_source():
    original = "Block A.\n\nBlock B."
    elements = json.dumps(
        [
            {
                "markdown_start": 0,
                "markdown_end": 8,
                "source_location": {"slide_number": 1, "shape_index": 2},
            },
            {
                "markdown_start": 10,
                "markdown_end": len(original),
                "source_location": {"slide_number": 1, "shape_index": 3},
            },
        ]
    )
    result = process_document_text(
        original,
        mode="ai_assisted",
        source_type="presentation",
        elements_json=elements,
        settings=_openai_settings(),
        chat_model=FakeChat(),
    )
    location = json.loads(result.elements_json)[0]["source_location"]
    assert location["slide_number"] == 1
    assert "shape_index" not in location
    assert [item["shape_index"] for item in location["source_locations"]] == [2, 3]


def test_input_limit_and_provider_errors_are_bounded_and_redacted():
    chat = FakeChat()
    with pytest.raises(DocumentProcessingError, match="document_processing_limit"):
        process_document_text(
            "A" * 200001,
            mode="ai_assisted",
            source_type="text",
            settings=_openai_settings(),
            chat_model=chat,
        )
    assert chat.inputs == []

    def fail(_):
        raise RuntimeError("provider-secret document-private-text")

    with pytest.raises(DocumentProcessingError) as error:
        process_document_text(
            "Private text",
            mode="ai_assisted",
            source_type="text",
            settings=_openai_settings(),
            chat_model=FakeChat(fail),
        )
    assert error.value.code == "document_processing_unavailable"
    assert "provider-secret" not in str(error.value)
    assert "document-private-text" not in str(error.value)


@pytest.fixture
def processing_db(tmp_path):
    import_all_models()
    engine = create_engine(f"sqlite+pysqlite:///{tmp_path / 'processing.db'}")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        db.add(
            UserModel(id="owner", email="owner@example.com", nickname="Owner", password_hash="x")
        )
        db.flush()
        db.add(KnowledgeBaseModel(id="kb", name="KB", scope="personal", owner_user_id="owner"))
        db.flush()
        document = DocumentModel(
            id="doc",
            title="Original",
            content="# Original\n\nKeep the original answer.",
            source_type="markdown",
            owner_user_id="owner",
            knowledge_base_id="kb",
        )
        db.add(document)
        db.commit()
        yield db, document
    engine.dispose()


def _index_snapshot(db, document):
    return {
        "content": document.content,
        "provenance": document.processing_provenance_json,
        "mode": document.ingestion_mode,
        "processed_at": document.processed_at,
        "chunks": list(db.scalars(select(DocumentChunkModel.id)).all()),
        "profiles": list(db.scalars(select(DocumentMetadataProfileModel.id)).all()),
        "artifacts": list(db.scalars(select(DocumentParseArtifactModel.id)).all()),
    }


@pytest.mark.parametrize("failure_phase", ["processing", "chunk_embedding", "metadata_embedding"])
def test_failed_reprocessing_preserves_the_entire_active_index(
    processing_db, monkeypatch, failure_phase
):
    db, document = processing_db
    service = KnowledgeExtractionService(db)
    service.ingest_document(document)
    before = _index_snapshot(db, document)
    run = service.create_extraction_run(
        document.id,
        ingestion_mode="ai_assisted",
        candidate_content="# Replacement\n\nOnly this candidate changes.",
    )
    if failure_phase == "processing":

        def fail(*args, **kwargs):
            raise DocumentProcessingError("document_processing_unavailable", "Unavailable.")

        monkeypatch.setattr("my_agents.knowledge.extraction.process_document_text", fail)
    else:

        class FailingProvider:
            provider = "deterministic"
            model = "fixture"
            dimensions = 32

            def __init__(self):
                self.calls = 0

            def embed_documents(self, texts):
                self.calls += 1
                if self.calls == (1 if failure_phase == "chunk_embedding" else 2):
                    # Another session still sees the entire old representation even
                    # after the writer has staged index deletion/replacement.
                    with Session(db.get_bind()) as observer:
                        visible = observer.get(DocumentModel, document.id)
                        assert _index_snapshot(observer, visible) == before
                    raise RuntimeError("provider-secret")
                return [deterministic_embedding(text) for text in texts]

        service._embedding_provider = FailingProvider()
    with pytest.raises((RuntimeError, DocumentProcessingError)):
        service.ingest_document(document, run=run)
    db.refresh(document)
    assert _index_snapshot(db, document) == before
    db.refresh(run)
    assert run.status == "failed"
    assert run.candidate_content is None
    assert "provider-secret" not in run.error


def test_successful_reprocessing_swaps_content_index_and_provenance_together(processing_db):
    db, document = processing_db
    service = KnowledgeExtractionService(db)
    service.ingest_document(document)
    original_chunk_ids = _index_snapshot(db, document)["chunks"]
    replacement = "# State 설계 원칙\n\n다운스트림, 감사, 복구에 필요한 값만 저장합니다.\n"
    run = service.create_extraction_run(
        document.id, ingestion_mode="ai_assisted", candidate_content=replacement
    )
    result = service.ingest_document(document, run=run)
    assert result.run.status == "completed"
    assert document.content == replacement
    assert document.ingestion_mode == "ai_assisted"
    provenance = json.loads(document.processing_provenance_json)
    assert provenance["provider"] == "deterministic"
    assert provenance["input_sha256"] == hashlib.sha256(replacement.encode()).hexdigest()
    artifacts = db.scalars(
        select(DocumentParseArtifactModel).order_by(DocumentParseArtifactModel.created_at)
    ).all()
    assert json.loads(artifacts[0].processing_provenance_json)["mode"] == "standard"
    assert json.loads(artifacts[-1].processing_provenance_json) == provenance
    assert all(
        chunk_id not in original_chunk_ids for chunk_id in _index_snapshot(db, document)["chunks"]
    )
    chunk = db.scalar(select(DocumentChunkModel))
    assert "State 설계 원칙" in chunk.content
    assert "다운스트림" in chunk.content
    assert document.content[chunk.start_offset : chunk.end_offset] == chunk.content


def test_reindex_does_not_run_ai_again_and_concurrent_admission_is_rejected(
    processing_db, monkeypatch
):
    db, document = processing_db
    document.ingestion_mode = "ai_assisted"
    db.commit()
    service = KnowledgeExtractionService(db)
    service.ingest_document(document)
    provenance = document.processing_provenance_json
    monkeypatch.setattr(
        "my_agents.knowledge.extraction.process_document_text",
        lambda *args, **kwargs: pytest.fail("Re-index must not process an active document again"),
    )
    queued = service.create_extraction_run(document.id)
    assert queued.candidate_content is None
    assert queued.ingestion_mode == "ai_assisted"
    with pytest.raises(ExtractionAlreadyRunning):
        service.create_extraction_run(document.id)
    db.rollback()
    assert len(db.scalars(select(ExtractionRunModel)).all()) == 2
    service.ingest_document(document, run=queued)
    assert document.processing_provenance_json == provenance


def test_markdown_heading_and_body_stay_together_with_exact_leading_offsets():
    content = "\n  \n# State 설계 원칙\n\nmessages + add_messages를 사용합니다.\n"
    chunks = _chunk_document_text(DocumentModel(content=content, source_type="markdown"))
    assert len(chunks) == 1
    text, start, end, *_ = chunks[0]
    assert "State 설계 원칙" in text and "add_messages" in text
    assert content[start:end] == text


def test_markdown_heading_does_not_become_an_orphan_before_a_long_body():
    content = "# State\n\n\n" + "Principle " * 200
    chunks = _chunk_document_text(DocumentModel(content=content, source_type="markdown"))
    assert "# State" in chunks[0][0] and "Principle" in chunks[0][0]
    assert all(content[start:end] == chunk for chunk, start, end, *_ in chunks)


def test_user_edit_during_processing_is_not_overwritten(processing_db, monkeypatch):
    db, document = processing_db
    service = KnowledgeExtractionService(db)
    service.ingest_document(document)
    before_chunks = list(db.scalars(select(DocumentChunkModel.id)).all())
    run = service.create_extraction_run(document.id, candidate_content="Candidate replacement")
    original_process = process_document_text

    def process_and_edit(*args, **kwargs):
        candidate = original_process(*args, **kwargs)
        with Session(db.get_bind()) as editor:
            current = editor.get(DocumentModel, document.id)
            current.content = "New user edit"
            editor.commit()
        return candidate

    monkeypatch.setattr("my_agents.knowledge.extraction.process_document_text", process_and_edit)
    with pytest.raises(DocumentProcessingError, match="document_changed_during_processing"):
        service.ingest_document(document, run=run)
    db.refresh(document)
    assert document.content == "New user edit"
    assert list(db.scalars(select(DocumentChunkModel.id)).all()) == before_chunks
