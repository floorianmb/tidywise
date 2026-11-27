"""Connecting AI assistants to the MCP server (`tidywise mcp`): Claude Code, Codex, OpenCode, Cursor, Windsurf,
Zed and Claude Desktop – and a copy-and-paste configuration for everything else.

Each assistant is registered through its own command line where that exists, so Tidywise never has to know
the format of someone else's settings file. Apps without one (Cursor, Windsurf, Zed, Claude Desktop) get a
single entry added to their JSON settings file; everything else in that file – other servers, comments,
formatting – stays as it was. Supporting another assistant = adding an entry to CLIENTS.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tomllib
from dataclasses import dataclass
from pathlib import Path

NAME = "tidywise"
HOME = Path.home()
# Where command-line tools usually live when they are not on the PATH of a Mac app.
SEARCH_PATH = [HOME / ".local/bin", Path("/opt/homebrew/bin"), Path("/usr/local/bin"), HOME / ".opencode/bin",
               HOME / ".npm-global/bin", HOME / ".bun/bin", HOME / ".cargo/bin", HOME / ".claude/local"]


class AssistantError(RuntimeError):
    """Connecting did not work. The text is written for people and shown in the app."""


def server_command() -> list[str]:
    """The command an assistant runs to start the server: a stable path to `tidywise`, plus `mcp`."""
    for candidate in (os.environ.get("TIDYWISE_CLI"), str(HOME / ".local/bin/tidywise"), shutil.which("tidywise"),
                      str(Path(sys.argv[0]).resolve())):
        if candidate and os.access(candidate, os.X_OK):
            return [candidate, "mcp"]
    return ["tidywise", "mcp"]


def _find(binary: str) -> str | None:
    path = os.pathsep.join([os.environ.get("PATH", "")] + [str(p) for p in SEARCH_PATH])
    return shutil.which(binary, path=path)


def _run(args: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(args, capture_output=True, text=True, timeout=60, stdin=subprocess.DEVNULL)


def _json(path: Path) -> dict:
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return {}


@dataclass(frozen=True)
class Client:
    id: str
    title: str

    @property
    def installed(self) -> bool:
        raise NotImplementedError

    def connected(self) -> bool:
        raise NotImplementedError

    def register(self, command: list[str]) -> None:
        """Add (or replace) the Tidywise entry."""
        raise NotImplementedError

    def unregister(self) -> None:
        raise NotImplementedError

    def next_step(self) -> str:
        return "Start a new session there to use it."


@dataclass(frozen=True)
class CliClient(Client):
    """An assistant with its own `mcp add` / `mcp remove` commands."""
    binary: str

    @property
    def path(self) -> str | None:
        return _find(self.binary)

    @property
    def installed(self) -> bool:
        return self.path is not None

    def add_args(self, cli: str, command: list[str]) -> list[str]:
        raise NotImplementedError

    def remove(self, cli: str) -> None:
        raise NotImplementedError

    def register(self, command: list[str]) -> None:
        cli = self._cli()
        if self.connected():
            self.remove(cli)  # replace an older entry, e.g. after Tidywise moved
        r = _run(self.add_args(cli, command))
        if r.returncode != 0:
            raise AssistantError(f"{self.title} did not accept the server: {(r.stderr or r.stdout).strip()[:300]}")

    def unregister(self) -> None:
        self.remove(self._cli())

    def _cli(self) -> str:
        if (cli := self.path) is None:
            raise AssistantError(f"{self.title} was not found on this Mac.")
        return cli


class ClaudeCode(CliClient):
    def connected(self) -> bool:
        return NAME in (_json(HOME / ".claude.json").get("mcpServers") or {})

    def add_args(self, cli: str, command: list[str]) -> list[str]:
        return [cli, "mcp", "add", "--scope", "user", NAME, "--", *command]

    def remove(self, cli: str) -> None:
        r = _run([cli, "mcp", "remove", "--scope", "user", NAME])
        if r.returncode != 0:
            raise AssistantError(f"{self.title} could not remove the server: {(r.stderr or r.stdout).strip()[:300]}")


class Codex(CliClient):
    config = HOME / ".codex/config.toml"

    def connected(self) -> bool:
        try:
            return NAME in (tomllib.loads(self.config.read_text()).get("mcp_servers") or {})
        except (OSError, ValueError):
            return False

    def add_args(self, cli: str, command: list[str]) -> list[str]:
        return [cli, "mcp", "add", NAME, "--", *command]

    def remove(self, cli: str) -> None:
        r = _run([cli, "mcp", "remove", NAME])
        if r.returncode != 0:
            raise AssistantError(f"{self.title} could not remove the server: {(r.stderr or r.stdout).strip()[:300]}")


def _strip_jsonc_comments(text: str) -> str:
    """Remove // and /* */ comments outside string literals so JSONC can be inspected safely."""
    pattern = re.compile(r'"(?:\\.|[^"\\])*"|//[^\n]*|/\*.*?\*/', re.DOTALL)
    return pattern.sub(lambda m: m.group(0) if m.group(0).startswith('"') else "", text)


class OpenCode(CliClient):
    configs = [HOME / ".config/opencode/opencode.json", HOME / ".config/opencode/opencode.jsonc"]

    def _servers(self, data: dict) -> dict:
        mcp = data.get("mcp") or {}
        return mcp.get("servers") if isinstance(mcp.get("servers"), dict) else mcp  # newer and older layout

    def _matched_config(self) -> Path | None:
        for p in self.configs:
            data = _json(p)
            if data:
                if NAME in self._servers(data):
                    return p
            else:
                stripped = _strip_jsonc_comments(_text(p))
                try:
                    parsed = json.loads(re.sub(r",(\s*[}\]])", r"\1", stripped)) if stripped.strip() else {}
                except ValueError:
                    parsed = None
                if isinstance(parsed, dict):
                    if NAME in self._servers(parsed):
                        return p
                elif f'"{NAME}"' in stripped:
                    return p
        return None

    def connected(self) -> bool:
        return self._matched_config() is not None

    def add_args(self, cli: str, command: list[str]) -> list[str]:
        return [cli, "mcp", "add", "--global", NAME, "--", *command]

    def remove(self, cli: str) -> None:
        # OpenCode has no command for this; its settings file is plain JSON we can edit safely.
        for p in self.configs:
            data = _json(p)
            servers = self._servers(data) if data else {}
            if NAME in servers:
                del servers[NAME]
                p.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n")
                return
        if (matched := self._matched_config()) is not None:
            raise AssistantError(f"Please remove “{NAME}” from the “mcp” section of {matched} by hand.")


def _text(path: Path) -> str:
    try:
        return path.read_text()
    except OSError:
        return ""


@dataclass(frozen=True)
class ConfigFileClient(Client):
    """An app that reads its MCP servers from a JSON (or JSONC) settings file."""
    config: Path
    key: str  # the object that holds the servers
    apps: tuple[str, ...]  # app bundles that show it is installed
    restart: bool = False  # only reads its settings on launch

    @property
    def installed(self) -> bool:
        apps = any((folder / app).exists() for folder in (Path("/Applications"), HOME / "Applications") for app in self.apps)
        return apps or self.config.parent.is_dir()

    def entry(self, command: list[str]) -> dict:
        return {"command": command[0], "args": command[1:]}

    def connected(self) -> bool:
        try:
            return jsonc_has(_text(self.config), [self.key, NAME])
        except ValueError:
            return f'"{NAME}"' in _strip_jsonc_comments(_text(self.config))

    def register(self, command: list[str]) -> None:
        self._edit(lambda text: jsonc_put(text, [self.key, NAME], self.entry(command)))

    def unregister(self) -> None:
        if self.config.exists():
            self._edit(lambda text: jsonc_remove(text, [self.key, NAME]))

    def next_step(self) -> str:
        return f"Quit and reopen {self.title} to use it." if self.restart else super().next_step()

    def _edit(self, change) -> None:
        if self.config.exists() and not os.access(self.config, os.R_OK | os.W_OK):
            raise AssistantError(f"Tidywise may not change {_home_short(self.config)}.")
        text = _text(self.config)
        try:
            new = change(text)
        except ValueError:
            raise AssistantError(f"{_home_short(self.config)} could not be read as JSON, so Tidywise left it alone. "
                                 f"Add “{NAME}” under “{self.key}” by hand (see Any other assistant).") from None
        if new == text:
            return
        self.config.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.config.with_name(f".{self.config.name}.tidywise-tmp")
        tmp.write_text(new)
        if self.config.exists():
            os.chmod(tmp, self.config.stat().st_mode & 0o7777)
        os.replace(tmp, self.config)  # never leave a half-written settings file behind


@dataclass(frozen=True)
class Zed(ConfigFileClient):
    def entry(self, command: list[str]) -> dict:
        return {"command": command[0], "args": command[1:], "env": {}}


def _home_short(path: Path) -> str:
    try:
        return "~/" + str(path.relative_to(HOME))
    except ValueError:
        return str(path)


# --- Editing JSON with comments (JSONC) without losing anything ---------------------------------------
# Settings files such as Zed's contain comments and trailing commas, which `json` would refuse or throw away.
# These helpers find a key's exact place in the text and change only that.

_GAP = re.compile(r"(?:\s+|//[^\n]*|/\*.*?\*/)*", re.DOTALL)
_STRING = re.compile(r'"(?:\\.|[^"\\])*"')
_SCALAR = re.compile(r"-?[0-9][0-9.eE+-]*|true|false|null")


def _skip(text: str, i: int) -> int:
    return _GAP.match(text, i).end()


def _members(text: str, start: int) -> tuple[list[tuple[str, int, int, int]], int]:
    """The members of the object or array opening at `start`: (key, key start, value start, value end) each,
    and the index just after the closing bracket. Raises ValueError on anything that is not JSON(C)."""
    close = {"{": "}", "[": "]"}[text[start]]
    members: list[tuple[str, int, int, int]] = []
    i = _skip(text, start + 1)
    while text[i:i + 1] != close:
        key, key_start = "", i
        if close == "}":
            if not (m := _STRING.match(text, i)):
                raise ValueError(f"expected a key at {i}")
            key = json.loads(m.group(0))
            i = _skip(text, m.end())
            if text[i:i + 1] != ":":
                raise ValueError(f"expected ':' at {i}")
            i = _skip(text, i + 1)
        value_start, i = i, _value_end(text, i)
        members.append((key, key_start, value_start, i))
        i = _skip(text, i)
        if text[i:i + 1] == ",":
            i = _skip(text, i + 1)
        elif text[i:i + 1] != close:
            raise ValueError(f"expected ',' or '{close}' at {i}")
    return members, i + 1


def _value_end(text: str, i: int) -> int:
    c = text[i:i + 1]
    if c in ("{", "["):
        return _members(text, i)[1]
    m = (_STRING if c == '"' else _SCALAR).match(text, i)
    if not m:
        raise ValueError(f"expected a value at {i}")
    return m.end()


def _root(text: str) -> int:
    i = _skip(text, 0)
    if text[i:i + 1] != "{":
        raise ValueError("the settings are not a JSON object")
    if _skip(text, _value_end(text, i)) != len(text):
        raise ValueError("unexpected text after the settings")
    return i


def _member(text: str, start: int, key: str) -> tuple[str, int, int, int] | None:
    return next((m for m in _members(text, start)[0] if m[0] == key), None)


def _object_path(text: str, keys: list[str]) -> int | None:
    """Where the object at `keys` opens, or None if one of them is missing."""
    obj = _root(text)
    for key in keys:
        member = _member(text, obj, key)
        if member is None:
            return None
        if text[member[2]] != "{":
            raise ValueError(f"“{key}” is not an object")
        obj = member[2]
    return obj


def _indent_of(text: str, i: int) -> str:
    line = text[text.rfind("\n", 0, i) + 1:i]
    return line[:len(line) - len(line.lstrip())]


def jsonc_has(text: str, keys: list[str]) -> bool:
    if not text.strip():
        return False
    obj = _object_path(text, keys[:-1])
    return obj is not None and _member(text, obj, keys[-1]) is not None


def jsonc_put(text: str, keys: list[str], value) -> str:
    """Set `keys` (a path of object keys) to `value`, creating the objects on the way."""
    if not text.strip():
        text = "{}\n"
    obj = _root(text)
    for depth, key in enumerate(keys):
        last = depth == len(keys) - 1
        member = _member(text, obj, key)
        if member is not None and last:
            return text[:member[2]] + _dump(value, _indent_of(text, member[1])) + text[member[3]:]
        if member is not None:
            if text[member[2]] != "{":
                raise ValueError(f"“{key}” is not an object")
            obj = member[2]
            continue
        members = _members(text, obj)[0]
        indent = _indent_of(text, members[0][1]) if members else _indent_of(text, obj) + "  "
        new = f"\n{indent}{json.dumps(key)}: " + (_dump(value, indent) if last else "{}")
        new += "," if members else "\n" + _indent_of(text, obj)
        text = text[:obj + 1] + new + text[obj + 1:]
        if last:
            return text
        obj = _member(text, obj, key)[2]
    return text


def jsonc_remove(text: str, keys: list[str]) -> str:
    if not text.strip() or (obj := _object_path(text, keys[:-1])) is None:
        return text
    members = _members(text, obj)[0]
    for n, (key, key_start, _, value_end) in enumerate(members):
        if key != keys[-1]:
            continue
        start = len(text[:key_start].rstrip(" \t\n\r"))
        after = _skip(text, value_end)
        if text[after:after + 1] == ",":
            return text[:start] + text[after + 1:]
        if n > 0:  # the last member: drop the comma in front of it instead
            return text[:members[n - 1][3]] + text[value_end:]
        return text[:start] + text[value_end:]
    return text


def _dump(value, indent: str) -> str:
    return json.dumps(value, indent=2, ensure_ascii=False).replace("\n", "\n" + indent)


APP_SUPPORT = HOME / "Library/Application Support"

CLIENTS: list[Client] = [
    ClaudeCode("claude", "Claude Code", "claude"),
    Codex("codex", "Codex", "codex"),
    OpenCode("opencode", "OpenCode", "opencode"),
    ConfigFileClient("claude-desktop", "Claude Desktop", APP_SUPPORT / "Claude/claude_desktop_config.json",
                     "mcpServers", ("Claude.app",), restart=True),
    ConfigFileClient("cursor", "Cursor", HOME / ".cursor/mcp.json", "mcpServers", ("Cursor.app",)),
    ConfigFileClient("windsurf", "Windsurf", HOME / ".codeium/windsurf/mcp_config.json", "mcpServers",
                     ("Windsurf.app",)),
    Zed("zed", "Zed", HOME / ".config/zed/settings.json", "context_servers", ("Zed.app", "Zed Preview.app")),
]


def client(client_id: str) -> Client:
    for c in CLIENTS:
        if c.id == client_id:
            return c
    raise AssistantError(f"Unknown assistant “{client_id}”. Available: {', '.join(c.id for c in CLIENTS)}")


def connect(client_id: str) -> str:
    """Register the server with an assistant. Returns a sentence saying what happened."""
    c = client(client_id)
    if not c.installed:
        raise AssistantError(f"{c.title} was not found on this Mac. Install it first, or use the manual configuration.")
    c.register(server_command())
    return f"{c.title} can now search your archive. {c.next_step()}"


def disconnect(client_id: str) -> str:
    c = client(client_id)
    if not c.installed and not c.connected():
        raise AssistantError(f"{c.title} was not found on this Mac.")
    c.unregister()
    return f"Removed from {c.title}."


def manual_snippet() -> str:
    """The configuration most other assistants understand (the common `mcpServers` format)."""
    command = server_command()
    return json.dumps({"mcpServers": {NAME: {"command": command[0], "args": command[1:]}}}, indent=2)


def status() -> dict:
    """Everything the app needs to draw its “Assistants” page."""
    command = server_command()
    return {
        "command": command,
        "command_line": " ".join(command),
        "snippet": manual_snippet(),
        "clients": [{"id": c.id, "title": c.title, "installed": c.installed, "connected": c.connected()} for c in CLIENTS],
    }
