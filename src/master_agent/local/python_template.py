"""Dependency-free starting point, then customized with BisCode's code tools."""

import json

AGENT_SOURCE = '''"""Local Ollama agent. Run --serve for an HTTP endpoint, or pass a message."""
import argparse
import hmac
import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

CONFIG = json.loads(Path(__file__).with_name("agent.json").read_text(encoding="utf-8"))


def respond(message):
    if not isinstance(message, str) or not message.strip() or len(message) > 12000:
        raise ValueError("message must contain 1-12000 characters")
    payload = {
        "model": os.getenv("OLLAMA_MODEL", CONFIG["model"]),
        "stream": False, "think": False,
        "messages": [
            {"role": "system", "content": CONFIG["instruction"]},
            {"role": "user", "content": message},
        ],
    }
    base = os.getenv("OLLAMA_BASE_URL", CONFIG["ollama_base_url"]).rstrip("/")
    req = Request(base + "/api/chat", data=json.dumps(payload).encode(),
                  headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urlopen(req, timeout=180) as response:
            result = json.loads(response.read(2_000_000))
        reply = result["message"]["content"]
        if not isinstance(reply, str):
            raise ValueError("Invalid model response")
        return reply
    except (HTTPError, URLError, TimeoutError, KeyError, ValueError) as exc:
        raise RuntimeError("Ollama request failed; check the local model and service") from exc


class Handler(BaseHTTPRequestHandler):
    def reply(self, status, body):
        raw = json.dumps(body, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self):
        self.reply(200 if self.path == "/healthz" else 404,
                   {"status": "ok"} if self.path == "/healthz" else {"error": "Not found"})

    def do_POST(self):
        if self.path != "/chat":
            self.reply(404, {"error": "Not found"})
            return
        key = os.getenv("AGENT_API_KEY", "")
        supplied = self.headers.get("Authorization", "")
        if key and not hmac.compare_digest(supplied.encode(), ("Bearer " + key).encode()):
            self.reply(401, {"error": "Unauthorized"})
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if not 0 < length <= 65536:
                raise ValueError("Invalid body length")
            body = json.loads(self.rfile.read(length))
            if not isinstance(body, dict):
                raise ValueError("Expected an object")
            reply = respond(body.get("message"))
        except (ValueError, TypeError):
            self.reply(400, {"error": "Expected a valid message"})
        except RuntimeError:
            self.reply(503, {"error": "Local model unavailable"})
        else:
            self.reply(200, {"reply": reply})

    def log_message(self, *args):
        pass


def main():
    parser = argparse.ArgumentParser(description=CONFIG["name"])
    parser.add_argument("message", nargs="?")
    parser.add_argument("--serve", action="store_true")
    parser.add_argument("--port", type=int, default=8081)
    args = parser.parse_args()
    if args.serve:
        server = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
        print(f"Agent listening on http://127.0.0.1:{server.server_port}", flush=True)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass
        finally:
            server.server_close()
    elif args.message:
        print(respond(args.message))
    else:
        parser.error("provide a message or --serve")


if __name__ == "__main__":
    main()
'''

TEST_SOURCE = """import io
import json
import unittest
from unittest.mock import patch

import agent


class AgentTest(unittest.TestCase):
    def test_message_is_sent_to_the_configured_local_model(self):
        response = io.BytesIO(json.dumps({"message": {"content": "پاسخ آزمایشی"}}).encode())
        with patch("agent.urlopen", return_value=response) as call:
            self.assertEqual(agent.respond("سلام"), "پاسخ آزمایشی")
        payload = json.loads(call.call_args.args[0].data)
        self.assertEqual(payload["messages"][0]["content"], agent.CONFIG["instruction"])
        self.assertEqual(payload["messages"][1]["content"], "سلام")
        self.assertFalse(payload["stream"])

    def test_invalid_input_never_calls_the_network(self):
        with patch("agent.urlopen") as call:
            for value in ["", "   ", None, 42, "a" * 12001]:
                with self.assertRaises(ValueError):
                    agent.respond(value)
            call.assert_not_called()


if __name__ == "__main__":
    unittest.main()
"""


def files(name, instruction, model, base_url):
    return {
        "agent.py": AGENT_SOURCE,
        "agent.json": json.dumps(
            {"name": name, "instruction": instruction, "model": model, "ollama_base_url": base_url},
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        "tests/test_agent.py": TEST_SOURCE,
        "README.md": f"""# {name}

ایجنت محلی Python با Ollama؛ پایهٔ گفتگو بدون حافظه است. قابلیت‌های اختصاصی را
در `agent.py` پیاده‌سازی و آزمایش کنید. متن دستورالعمل در `agent.json` است.

Python 3.11+ کافی است و وابستگی خارجی ندارد. Ollama باید جدا روشن باشد.

```bash
python agent.py 'سلام'
python agent.py --serve --port 8081
python -m unittest discover -s tests -v
```

API محلی: POST `/chat` با JSON `{{"message":"سلام"}}`؛ وضعیت: GET `/healthz`.
آدرس پیش‌فرض فقط 127.0.0.1 است. `AGENT_API_KEY` اختیاری، احراز هویت Bearer را
برای مسیر گفتگو فعال می‌کند. کلید را در فایل کد یا git نگذارید.
`OLLAMA_MODEL` و `OLLAMA_BASE_URL` تنظیمات پیش‌فرض agent.json را عوض می‌کنند.

تست‌ها ارتباط مدل را شبیه‌سازی می‌کنند؛ موفقیت آن‌ها صحت زندهٔ Ollama یا
یکپارچه‌سازی‌های سفارشی را اثبات نمی‌کند. اجرای سرویس جدا از ساخت پروژه است.
""",
        ".gitignore": "__pycache__/\n.venv/\n.env\n*.key\n*.session\n",
    }
