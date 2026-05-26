"""Smoke-test the local production-shaped V1 API demo path.

This command verifies the running backend through HTTP only. It assumes a file-backed
SQLite database has already been seeded with `python -m scripts.local_demo_seed`.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from http.cookiejar import CookieJar
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urljoin
from urllib.request import HTTPCookieProcessor, Request, build_opener

from scripts.local_demo_seed import DEMO_DOCUMENT_TITLE, DEMO_EMAIL, DEMO_PASSWORD

DEFAULT_BASE_URL = "http://localhost:8000"
DEFAULT_PROMPT = "How does the product chat service stream answers and persist app state?"
JsonObject = dict[str, Any]


@dataclass(frozen=True)
class SmokeResult:
    """Summary of a successful local V1 API smoke."""

    base_url: str
    email: str
    user_id: str
    document_id: str
    extraction_run_id: str
    conversation_id: str
    run_id: str
    answer_delta_count: int
    citation_count: int
    event_count: int


class SmokeFailure(RuntimeError):
    """Raised when the local V1 smoke path does not satisfy the contract."""


class ApiClient:
    """Small cookie-aware JSON/SSE HTTP client using only the standard library."""

    def __init__(self, base_url: str, timeout: float) -> None:
        self.base_url = base_url.rstrip("/") + "/"
        self.timeout = timeout
        self.cookie_jar = CookieJar()
        self.opener = build_opener(HTTPCookieProcessor(self.cookie_jar))

    def get_json(self, path: str) -> Any:
        body = self._request(path=path, method="GET")
        return json.loads(body)

    def post_json(self, path: str, payload: JsonObject) -> Any:
        body = self._request(path=path, method="POST", json_payload=payload)
        return json.loads(body)

    def post_bodyless_json(self, path: str) -> Any:
        body = self._request(path=path, method="POST")
        return json.loads(body)

    def post_sse(self, path: str, payload: JsonObject) -> list[JsonObject]:
        body = self._request(
            path=path,
            method="POST",
            json_payload=payload,
            headers={"Accept": "text/event-stream"},
        )
        return parse_sse_events(body)

    def _request(
        self,
        *,
        path: str,
        method: str,
        json_payload: JsonObject | None = None,
        headers: dict[str, str] | None = None,
    ) -> str:
        request_headers = dict(headers or {})
        data = None
        if json_payload is not None:
            data = json.dumps(json_payload).encode("utf-8")
            request_headers["Content-Type"] = "application/json"
        request = Request(
            urljoin(self.base_url, path.lstrip("/")),
            data=data,
            headers=request_headers,
            method=method,
        )
        try:
            with self.opener.open(request, timeout=self.timeout) as response:
                return response.read().decode("utf-8")
        except HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise SmokeFailure(f"{method} {path} failed with HTTP {exc.code}: {detail}") from exc
        except URLError as exc:
            raise SmokeFailure(f"{method} {path} failed: {exc.reason}") from exc


def run_smoke(
    *,
    base_url: str = DEFAULT_BASE_URL,
    email: str = DEMO_EMAIL,
    password: str = DEMO_PASSWORD,
    document_title: str = DEMO_DOCUMENT_TITLE,
    prompt: str = DEFAULT_PROMPT,
    timeout: float = 90.0,
) -> SmokeResult:
    """Run the local V1 API smoke against an already-running backend."""
    client = ApiClient(base_url=base_url, timeout=timeout)

    health = client.get_json("/health")
    if health.get("status") != "ok":
        raise SmokeFailure(f"unexpected health payload: {health!r}")

    login = client.post_json("/auth/login", {"email": email, "password": password})
    user = login.get("user")
    if not isinstance(user, dict) or user.get("email") != email:
        raise SmokeFailure(f"login did not return expected user payload: {login!r}")
    if not user.get("email_verified_at"):
        raise SmokeFailure("demo user is not verified; run scripts.local_demo_seed first")
    csrf_token = login.get("csrf_token")
    if not isinstance(csrf_token, str) or not csrf_token:
        raise SmokeFailure("login did not return a CSRF token")

    me = client.get_json("/auth/me")
    if me.get("email") != email:
        raise SmokeFailure(f"/auth/me did not return the logged-in user: {me!r}")

    documents = client.get_json("/documents")
    document = find_document_by_title(documents, document_title)
    document_id = document["id"]

    extraction_runs = client.get_json(f"/documents/{document_id}/extraction-runs")
    if not extraction_runs:
        raise SmokeFailure(
            "seeded document has no extraction runs; run scripts.local_demo_seed first"
        )

    ingest = client.post_bodyless_json(f"/documents/{document_id}/ingest")
    if ingest.get("status") != "completed" or ingest.get("chunk_count", 0) < 1:
        raise SmokeFailure(f"bodyless ingest did not complete with chunks: {ingest!r}")

    conversation = client.post_json("/conversations", {"title": "V1 API smoke"})
    conversation_id = conversation.get("id")
    if not isinstance(conversation_id, str) or not conversation_id:
        raise SmokeFailure(f"conversation creation failed: {conversation!r}")

    stream_events = client.post_sse(
        f"/conversations/{conversation_id}/runs/stream",
        {"message": prompt},
    )
    answer_delta_events = [event for event in stream_events if event.get("event") == "answer_delta"]
    completed = _last_event_payload(stream_events, "run_completed")
    if not answer_delta_events:
        raise SmokeFailure("SSE stream did not include answer_delta events")
    run_id = completed.get("run_id")
    if not isinstance(run_id, str) or not run_id:
        raise SmokeFailure(f"run_completed did not include run_id: {completed!r}")
    stream_citations = completed.get("citations")
    if not isinstance(stream_citations, list) or not stream_citations:
        raise SmokeFailure(f"run_completed did not include citations: {completed!r}")

    detail = client.get_json(f"/conversations/{conversation_id}/runs/{run_id}")
    detail_citations = detail.get("citations")
    if not isinstance(detail_citations, list) or not detail_citations:
        raise SmokeFailure(f"run detail did not persist citations: {detail!r}")
    if detail.get("reply") != completed.get("reply"):
        raise SmokeFailure("run detail reply does not match run_completed reply")

    run_events = client.get_json(f"/conversations/{conversation_id}/runs/{run_id}/events")
    assert_redacted_run_events(run_events, forbidden_text=[prompt])

    return SmokeResult(
        base_url=base_url.rstrip("/"),
        email=email,
        user_id=user["id"],
        document_id=document_id,
        extraction_run_id=ingest["id"],
        conversation_id=conversation_id,
        run_id=run_id,
        answer_delta_count=len(answer_delta_events),
        citation_count=len(detail_citations),
        event_count=len(run_events),
    )


def parse_sse_events(body: str) -> list[JsonObject]:
    """Parse simple Server-Sent Events with one event name and JSON data payload."""
    events: list[JsonObject] = []
    for raw_event in body.strip().split("\n\n"):
        if not raw_event.strip():
            continue
        event_name = ""
        data_lines: list[str] = []
        for line in raw_event.splitlines():
            if line.startswith("event: "):
                event_name = line.removeprefix("event: ")
            elif line.startswith("data: "):
                data_lines.append(line.removeprefix("data: "))
        if not event_name:
            raise SmokeFailure(f"SSE event is missing event name: {raw_event!r}")
        if not data_lines:
            raise SmokeFailure(f"SSE event is missing data payload: {raw_event!r}")
        events.append({"event": event_name, "data": json.loads("\n".join(data_lines))})
    return events


def find_document_by_title(documents: Any, title: str) -> JsonObject:
    """Return one document payload from a list by exact title."""
    if not isinstance(documents, list):
        raise SmokeFailure(f"/documents did not return a list: {documents!r}")
    matches = [item for item in documents if isinstance(item, dict) and item.get("title") == title]
    if not matches:
        raise SmokeFailure(f"seeded document {title!r} was not found; run scripts.local_demo_seed")
    document_id = matches[0].get("id")
    if not isinstance(document_id, str) or not document_id:
        raise SmokeFailure(f"seeded document payload is missing id: {matches[0]!r}")
    return matches[0]


def assert_redacted_run_events(run_events: Any, *, forbidden_text: list[str]) -> None:
    """Assert run events are present and do not leak raw prompt-like text."""
    if not isinstance(run_events, list) or len(run_events) < 4:
        raise SmokeFailure(f"run events are missing or incomplete: {run_events!r}")
    event_types = [event.get("event_type") for event in run_events if isinstance(event, dict)]
    expected = ["user_message_stored", "retrieval_completed", "graph_invoked", "answer_composed"]
    missing = [event_type for event_type in expected if event_type not in event_types]
    if missing:
        raise SmokeFailure(f"run events are missing expected redacted events: {missing!r}")
    serialized = json.dumps(run_events, sort_keys=True)
    for text in forbidden_text:
        if text and text in serialized:
            raise SmokeFailure("run events leaked forbidden raw text")


def _last_event_payload(events: list[JsonObject], event_name: str) -> JsonObject:
    matches = [event["data"] for event in events if event.get("event") == event_name]
    if not matches:
        raise SmokeFailure(f"SSE stream did not include {event_name}")
    payload = matches[-1]
    if not isinstance(payload, dict):
        raise SmokeFailure(f"SSE {event_name} payload is not an object: {payload!r}")
    return payload


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Smoke-test the local V1 API demo path against a running backend."
    )
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL, help="Running backend base URL.")
    parser.add_argument("--email", default=DEMO_EMAIL, help="Seeded demo account email.")
    parser.add_argument("--password", default=DEMO_PASSWORD, help="Seeded demo account password.")
    parser.add_argument(
        "--document-title",
        default=DEMO_DOCUMENT_TITLE,
        help="Seeded document title to verify and ingest.",
    )
    parser.add_argument("--prompt", default=DEFAULT_PROMPT, help="Conversation prompt.")
    parser.add_argument("--timeout", type=float, default=90.0, help="HTTP timeout seconds.")
    return parser


def main() -> int:
    args = _build_parser().parse_args()
    try:
        result = run_smoke(
            base_url=args.base_url,
            email=args.email,
            password=args.password,
            document_title=args.document_title,
            prompt=args.prompt,
            timeout=args.timeout,
        )
    except SmokeFailure as exc:
        print(f"Local V1 API smoke failed: {exc}", file=sys.stderr)
        return 1

    print("Local V1 API smoke passed")
    print(f"base_url={result.base_url}")
    print(f"email={result.email}")
    print(f"user_id={result.user_id}")
    print(f"document_id={result.document_id}")
    print(f"extraction_run_id={result.extraction_run_id}")
    print(f"conversation_id={result.conversation_id}")
    print(f"run_id={result.run_id}")
    print(f"answer_delta_count={result.answer_delta_count}")
    print(f"citation_count={result.citation_count}")
    print(f"event_count={result.event_count}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
