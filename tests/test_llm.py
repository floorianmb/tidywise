"""Talking to the model server: Ollama and OpenAI-style local servers (LM Studio, mlx-lm, llama.cpp),
against a small fake server on 127.0.0.1.

    .venv/bin/python -m unittest discover tests
"""
import dataclasses
import json
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from tidywise import config, llm

SCHEMA = {"type": "object", "properties": {"topic": {"type": "string"}}, "required": ["topic"]}


class FakeServer(BaseHTTPRequestHandler):
    requests: list = []
    reject_schema = False
    models = ["qwen3-vl-8b"]

    def log_message(self, *args):
        pass

    def _answer(self, code: int, body: dict) -> None:
        data = json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        if self.path == "/v1/models":
            return self._answer(200, {"object": "list", "data": [{"id": m, "object": "model"} for m in self.models]})
        if self.path == "/api/tags":
            return self._answer(200, {"models": [{"name": "gemma4:e4b-it-qat"}]})
        self._answer(404, {"error": "not found"})

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        type(self).requests.append((self.path, body))
        if self.path == "/v1/chat/completions":
            if "response_format" in body and self.reject_schema:
                return self._answer(400, {"error": "response_format json_schema is not supported"})
            return self._answer(200, {"choices": [{"message": {"role": "assistant", "content": 'Sure: {"topic": "travel"}'}}],
                                      "usage": {"prompt_tokens": 120, "completion_tokens": 8}})
        if self.path == "/api/chat":
            return self._answer(200, {"message": {"content": '{"topic": "finance"}'}, "eval_count": 5})
        self._answer(404, {"error": "not found"})


class LLMTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), FakeServer)
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        cls.url = f"http://127.0.0.1:{cls.server.server_address[1]}"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()

    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        FakeServer.requests, FakeServer.reject_schema = [], False

    def tearDown(self):
        self.dir.cleanup()

    def cfg(self, **raw) -> config.Config:
        cfg = config._from_raw({"llm_backend": "openai", "llm_url": self.url + "/v1", "model": "qwen3-vl-8b",
                                "ollama_url": self.url, **raw})
        return dataclasses.replace(cfg, state_dir=Path(self.dir.name))

    def test_openai_style_server(self):
        cfg = self.cfg()
        self.assertEqual(llm.available(cfg), (True, "ok"))
        answer = llm.chat_json(cfg, "Sort it.", "A boarding pass", SCHEMA, images=["aGVsbG8="], kind="file", label="x")
        self.assertEqual(answer, {"topic": "travel"})
        path, body = FakeServer.requests[-1]
        self.assertEqual(path, "/v1/chat/completions")
        self.assertEqual(body["model"], "qwen3-vl-8b")
        self.assertEqual(body["response_format"]["json_schema"]["schema"], SCHEMA)
        self.assertEqual(body["messages"][1]["content"][1]["image_url"]["url"], "data:image/jpeg;base64,aGVsbG8=")

    def test_server_without_schema_support_gets_the_schema_in_the_prompt(self):
        FakeServer.reject_schema = True
        self.assertEqual(llm.chat_json(self.cfg(), "Sort it.", "A text", SCHEMA), {"topic": "travel"})
        _, body = FakeServer.requests[-1]
        self.assertNotIn("response_format", body)
        self.assertIn('"required": ["topic"]', body["messages"][0]["content"])
        self.assertEqual(body["messages"][1]["content"], "A text", "no images → plain text content")

    def test_unknown_model_and_missing_server_are_explained(self):
        ok, msg = llm.available(self.cfg(model="nope"))
        self.assertFalse(ok)
        self.assertIn("qwen3-vl-8b", msg, "lists what the server offers")
        ok, msg = llm.available(self.cfg(llm_url="http://127.0.0.1:9/v1"))
        self.assertFalse(ok)
        self.assertIn("LM Studio", msg)

    def test_ollama_stays_the_default(self):
        cfg = dataclasses.replace(config._from_raw({"ollama_url": self.url}), state_dir=Path(self.dir.name))
        self.assertEqual(cfg.llm_backend, "ollama")
        self.assertEqual(llm.available(cfg), (True, "ok"))
        self.assertEqual(llm.chat_json(cfg, "Sort it.", "An invoice", SCHEMA), {"topic": "finance"})
        self.assertEqual(FakeServer.requests[-1][0], "/api/chat")

    def test_nothing_is_sent_to_another_computer(self):
        for raw in ({"llm_url": "http://192.168.1.20:1234/v1"},
                    {"llm_backend": "ollama", "ollama_url": "http://gpu-box.local:11434"}):
            with self.subTest(raw=raw):
                cfg = self.cfg(**raw)
                ok, msg = llm.available(cfg)
                self.assertFalse(ok)
                self.assertIn("not on this Mac", msg)
                with self.assertRaises(llm.LLMError):
                    llm.chat_json(cfg, "s", "u", SCHEMA)
        self.assertEqual(len(FakeServer.requests), 0)
        self.assertTrue(config.is_local_url("http://localhost:1234/v1"))
        self.assertTrue(config.is_local_url("http://[::1]:8080/v1"))
        self.assertFalse(config.is_local_url("http://127.0.0.1.evil.example/v1"))


class BackendConfigTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.path = Path(self.dir.name) / "config.toml"

    def tearDown(self):
        self.dir.cleanup()

    def raw(self, **changes) -> dict:
        return {**config.to_dict(config.load(self.path), self.path), "root": self.dir.name + "/Tidywise", **changes}

    def test_backend_settings_round_trip(self):
        config.save(self.raw(llm_backend="openai", llm_url="http://127.0.0.1:8080/v1/", model="mlx-community/Qwen3"), self.path)
        cfg = config.load(self.path)
        self.assertEqual((cfg.llm_backend, cfg.llm_url, cfg.model), ("openai", "http://127.0.0.1:8080/v1", "mlx-community/Qwen3"))
        self.assertEqual(cfg.llm_endpoint, "http://127.0.0.1:8080/v1")

    def test_remote_or_unknown_servers_are_refused(self):
        for change in ({"llm_backend": "lmstudio"}, {"llm_url": "ftp://127.0.0.1"},
                       {"llm_url": "http://10.0.0.5:1234/v1"}, {"ollama_url": "https://ollama.example.com"}):
            with self.assertRaises(config.ConfigError, msg=str(change)):
                config.save(self.raw(**change), self.path)
        config.save(self.raw(llm_url="http://10.0.0.5:1234/v1", allow_remote_llm=True), self.path)
        self.assertTrue(config.load(self.path).allow_remote_llm)


if __name__ == "__main__":
    unittest.main()
