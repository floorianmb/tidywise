"""Talking to the local model: through Ollama (the default) or any local server with an OpenAI-style API –
LM Studio, mlx-lm (`mlx_lm.server`), llama.cpp (`llama-server`).

Either way the server has to run on this Mac unless the user explicitly set `allow_remote_llm = true`:
file contents never leave the computer by accident.
"""
from __future__ import annotations

import json
import time
import urllib.error
import urllib.request

from . import telemetry
from .config import Config, is_local_url


class LLMError(RuntimeError):
    pass


def _name(cfg: Config) -> str:
    return "Ollama" if cfg.llm_backend == "ollama" else "The model server"


def _base(cfg: Config) -> str:
    url = cfg.llm_endpoint
    if not cfg.allow_remote_llm and not is_local_url(url):
        raise LLMError(f"{url} is not on this Mac, so Tidywise won't send anything there. "
                       "Use a server on 127.0.0.1 – or set allow_remote_llm = true if you really mean it.")
    return url


def _request(cfg: Config, path: str, payload: dict | None, timeout: float) -> dict:
    base = _base(cfg)
    req = urllib.request.Request(base + path, data=json.dumps(payload).encode() if payload is not None else None,
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as e:
        with e:
            detail = e.read().decode(errors="replace")[:300]
        raise LLMError(f"{_name(cfg)} {e.code}: {detail}") from e
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        raise LLMError(f"{_name(cfg)} not reachable at {base}: {e}") from e
    except ValueError as e:
        raise LLMError(f"{_name(cfg)} at {base} did not answer with JSON: {e}") from e


def available(cfg: Config) -> tuple[bool, str]:
    if cfg.llm_backend != "ollama":
        return _available_openai(cfg)
    try:
        names = [m["name"] for m in _request(cfg, "/api/tags", None, 5).get("models", [])]
    except (LLMError, AttributeError, KeyError, TypeError) as e:
        if "not on this Mac" in str(e):
            return False, str(e)
        return False, f"Ollama isn't running ({e}). Start it in the Tidywise app, or: brew services start ollama"
    wanted = {cfg.model, cfg.model + ":latest"}
    if not wanted & set(names):
        return False, f"Model {cfg.model} is missing. Download it in the Tidywise app, or: ollama pull {cfg.model}"
    return True, "ok"


def _available_openai(cfg: Config) -> tuple[bool, str]:
    try:
        ids = [m.get("id") for m in _request(cfg, "/models", None, 5).get("data", []) if isinstance(m, dict)]
    except (LLMError, AttributeError) as e:
        if "not on this Mac" in str(e):
            return False, str(e)
        return False, (f"No model server answers at {cfg.llm_url} ({e}). Start LM Studio's local server, "
                       "`mlx_lm.server` or `llama-server` – or switch back to Ollama: tidywise config set llm_backend ollama")
    if ids and cfg.model not in ids:
        return False, (f"The server at {cfg.llm_url} has no model “{cfg.model}”. It offers: {', '.join(ids[:8])}. "
                       f"Pick one with: tidywise config set model <name>")
    return True, "ok"


def chat_json(cfg: Config, system: str, user: str, schema: dict, images: list[str] | None = None,
              timeout: float = 300, kind: str = "", label: str = "") -> dict:
    try:
        if cfg.llm_backend == "ollama":
            data = _chat(cfg, system, user, schema, images, timeout)
            content = data.get("message", {}).get("content", "")
        else:
            data, content = _chat_openai(cfg, system, user, schema, images, timeout)
    except LLMError as e:
        telemetry.record_call(cfg, kind, label, None, len(images or []), error=str(e)[:300])
        raise
    telemetry.record_call(cfg, kind, label, data, len(images or []))
    return _parse(content)


def _schema_in_prompt(system: str, schema: dict) -> str:
    return (system + "\n\nAnswer only with a JSON object matching this schema, with no other text:\n"
            + json.dumps(schema, ensure_ascii=False))


def _chat(cfg: Config, system: str, user: str, schema: dict, images: list[str] | None, timeout: float) -> dict:
    msg: dict = {"role": "user", "content": user}
    if images:
        msg["images"] = images
    payload = {
        "model": cfg.model,
        "messages": [{"role": "system", "content": system}, msg],
        "format": schema,
        "stream": False,
        "think": False,  # classifying needs no long reasoning; saves a lot of time
        "keep_alive": "5m",
        "options": {"temperature": 0, "num_ctx": 8192},
    }
    try:
        data = _request(cfg, "/api/chat", payload, timeout)
    except LLMError as e:
        if "structured output" not in str(e):
            raise
        # Some runners (e.g. MLX) can't enforce a schema → put the schema in the prompt and parse the answer ourselves
        payload.pop("format")
        payload["messages"][0]["content"] = _schema_in_prompt(system, schema)
        data = _request(cfg, "/api/chat", payload, timeout)
    return data


def _chat_openai(cfg: Config, system: str, user: str, schema: dict, images: list[str] | None,
                 timeout: float) -> tuple[dict, str]:
    """One chat completion. Returns the answer in the shape telemetry knows from Ollama, and the text."""
    content: str | list = user
    if images:  # Tidywise sends images as base64 JPEG
        content = [{"type": "text", "text": user}] + [
            {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{img}"}} for img in images]
    payload: dict = {
        "model": cfg.model,
        "messages": [{"role": "system", "content": system}, {"role": "user", "content": content}],
        "temperature": 0,
        "stream": False,
        "response_format": {"type": "json_schema", "json_schema": {"name": "answer", "schema": schema}},
    }
    started = time.monotonic()
    try:
        data = _request(cfg, "/chat/completions", payload, timeout)
    except LLMError as e:
        if not any(f" {code}:" in str(e) for code in (400, 422, 500, 501)):
            raise
        # Not every server can enforce a schema (mlx-lm, older llama.cpp) → ask for JSON in the prompt instead
        payload.pop("response_format")
        payload["messages"][0]["content"] = _schema_in_prompt(system, schema)
        started = time.monotonic()
        data = _request(cfg, "/chat/completions", payload, timeout)
    elapsed_ns = (time.monotonic() - started) * 1e9
    try:
        text = data["choices"][0]["message"]["content"] or ""
    except (KeyError, IndexError, TypeError):
        raise LLMError(f"The model server answered without a message: {json.dumps(data)[:200]}") from None
    usage = data.get("usage") or {}
    stats = {"prompt_eval_count": usage.get("prompt_tokens"), "eval_count": usage.get("completion_tokens"),
             "total_duration": elapsed_ns, "eval_duration": elapsed_ns}
    return stats, text


def _parse(content: str) -> dict:
    try:
        return json.loads(content)
    except json.JSONDecodeError:
        pass
    start, end = content.find("{"), content.rfind("}")
    if start != -1 and end > start:
        try:
            return json.loads(content[start:end + 1])
        except json.JSONDecodeError:
            pass
    raise LLMError(f"No valid JSON answer: {content[:200]}")
