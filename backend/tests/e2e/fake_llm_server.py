"""OpenAI-compatible fake LLM server backed by the pytest fixtures.

Stdlib-only HTTP server speaking just enough of /v1/chat/completions to
drive the golden-path e2e run: it reads the user message text and serves
the sentence fixture when it carries the __SENTENCE_CASE__ marker, the
word fixture otherwise (plain containerized copy of the FakeLLM routing).

Run from the docker-compose `fake-llm` service as:

    python3 /app/fake_llm_server.py /fixtures

The optional argument is the fixtures directory; it defaults to
backend/tests/fixtures/llm so the pytest tests reuse this module.
"""

import json
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

DEFAULT_FIXTURES_DIR = (
    Path(__file__).resolve().parents[1] / "fixtures" / "llm"
)
SENTENCE_MARKER = "__SENTENCE_CASE__"
DEFAULT_PORT = 8000


def load_fixtures(fixtures_dir: Path) -> dict[str, dict]:
    """Load the word and sentence fixture payloads from `fixtures_dir`."""
    fixtures = {}
    for name in ("word", "sentence"):
        fixtures[name] = json.loads(
            (fixtures_dir / f"{name}.json").read_text(encoding="utf-8")
        )
    return fixtures


def make_handler(fixtures: dict[str, dict]) -> type[BaseHTTPRequestHandler]:
    """Build an HTTP handler class answering chat completions from fixtures."""

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            if self.path != "/v1/chat/completions":
                self._send(404, {"error": "not_found"})
                return
            try:
                request = json.loads(self._read_body().decode("utf-8"))
                user_text = "".join(
                    str(message["content"])
                    for message in request["messages"]
                    if message.get("role") == "user"
                )
            except (ValueError, KeyError, TypeError):
                self._send(400, {"error": "bad_request"})
                return
            fixture = (
                fixtures["sentence"]
                if SENTENCE_MARKER in user_text
                else fixtures["word"]
            )
            completion = {
                "id": "chatcmpl-fake",
                "object": "chat.completion",
                "model": "fake-llm",
                "choices": [
                    {
                        "index": 0,
                        "message": {
                            "role": "assistant",
                            "content": json.dumps(fixture),
                        },
                        "finish_reason": "stop",
                    }
                ],
            }
            self._send(200, completion)

        def do_GET(self) -> None:
            self._send(404, {"error": "not_found"})

        def _read_body(self) -> bytes:
            length = int(self.headers.get("Content-Length") or 0)
            return self.rfile.read(length)

        def _send(self, status: int, payload: dict) -> None:
            body = json.dumps(payload).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args: object) -> None:
            pass

    return Handler


def serve(fixtures_dir: Path, port: int = DEFAULT_PORT) -> None:
    """Serve fixtures until interrupted."""
    handler = make_handler(load_fixtures(fixtures_dir))
    server = ThreadingHTTPServer(("0.0.0.0", port), handler)
    server.serve_forever()


def main() -> None:
    fixtures_dir = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_FIXTURES_DIR
    serve(fixtures_dir)


if __name__ == "__main__":
    main()
