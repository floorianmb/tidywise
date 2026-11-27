"""MCP server: lets AI assistants (Claude Code, Codex, OpenCode, …) search the archive.

The assistant starts it itself as `tidywise mcp` and talks to it over stdin/stdout, so there is no open
port and nothing to authenticate. It reads the database only – it never moves or changes anything – and it
only answers while the Tidywise app is open and the user has allowed it in the app's settings.

Built to cost little: two tools with short descriptions, answers as compact text lines with a cap,
no third-party imports, one read-only SQLite query per call.
"""
from __future__ import annotations

import json
import os
import sqlite3
import sys
from datetime import datetime, timedelta
from pathlib import Path

from . import __version__, config

PROTOCOL_VERSIONS = ("2025-11-25", "2025-06-18", "2025-03-26", "2024-11-05")
MAX_LIMIT = 50

INSTRUCTIONS = (
    "Tidywise is the user's local archive on this Mac: files it has sorted into topic folders (with a one-line "
    "summary each) and, if the user turned it on, pages from their browser history. Use it only when the user "
    "asks about their own files, downloads or what they read. Results include paths you can open with your own "
    "file tools. Files moved or deleted outside Tidywise may still be indexed, but unavailable files are "
    "flagged and have no openable path. Never open or search the private folder (_private or _privat)."
)

TOOLS = [
    {
        "name": "search",
        "description": "The user's sorted files, pages read and web searches, newest first – by words, recent days or both.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Words that must all match; omit to list everything"},
                "days": {"type": "integer", "description": "Only the last N days"},
                "kind": {"type": "string", "enum": ["all", "files", "links", "searches"], "default": "all"},
                "topic": {"type": "string", "description": "Topic slug to restrict to (see `topics`)"},
                "limit": {"type": "integer", "default": 10, "maximum": MAX_LIMIT},
            },
        },
    },
    {
        "name": "topics",
        "description": "The user's topics with file and link counts, and where the archive lives.",
        "inputSchema": {"type": "object", "properties": {}},
    },
]


# --- May the server answer right now? --------------------------------------------------------------

def app_pid_path(cfg: config.Config) -> Path:
    """The app writes its process id here while it is open."""
    return cfg.state_dir / "app.pid"


def app_is_open(cfg: config.Config) -> bool:
    try:
        pid = int(app_pid_path(cfg).read_text().strip())
        os.kill(pid, 0)
        return True
    except (OSError, ValueError):
        return False


def refusal(cfg: config.Config, always: bool) -> str | None:
    """A sentence for the assistant if the archive is not available right now, else None."""
    if always:
        return None
    if not cfg.mcp_enabled:
        return ("The user has not allowed assistants to search Tidywise. They can turn it on in the Tidywise app "
                "under Settings → Assistants.")
    if not app_is_open(cfg):
        return "Tidywise is not open. Ask the user to open the Tidywise app – the archive is only available while it runs."
    return None


# --- Reading the archive ---------------------------------------------------------------------------

def _db(cfg: config.Config) -> sqlite3.Connection | None:
    if not cfg.db_path.exists():
        return None
    con = sqlite3.connect(f"file:{cfg.db_path}?mode=ro", uri=True, timeout=5)
    con.row_factory = sqlite3.Row
    return con


def _short(path: str | None) -> str:
    return config.short_path(path)


def _limit(args: dict, default: int) -> int:
    try:
        return max(1, min(MAX_LIMIT, int(args.get("limit", default))))
    except (TypeError, ValueError):
        return default


def _like(columns: str, words: list[str]) -> tuple[str, list[str]]:
    """SQL condition: every word occurs somewhere in the given columns."""
    return " AND ".join(f"({columns}) LIKE ? ESCAPE '\\'" for _ in words), [
        "%" + w.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%" for w in words]


def _is_inbox(cur_path: str | None) -> bool:
    return bool(cur_path) and Path(cur_path).parent.name == "_inbox"


def _file_line(r: sqlite3.Row) -> str:
    path = r["cur_path"]
    inbox = _is_inbox(path)
    keywords = f" [{r['keywords']}]" if r["keywords"] else ""
    try:
        available = bool(path) and Path(path).is_file()
    except OSError:
        available = False
    location = f" → {_short(path)}" if available else " (file unavailable at its indexed location)"
    return (f"#{r['id']} {'inbox' if inbox else r['topic']} · {(r['moved_at'] or '')[:10]} · {r['name']} — "
            f"{r['summary']}{keywords}{location}")


def _link_line(r: sqlite3.Row) -> str:
    visits = f" ({r['visits']}×)" if (r["visits"] or 0) > 1 else ""
    return f"{(r['last_visit'] or '')[:10]} · {r['topic'] or '–'} · {r['title']} — {r['url']}{visits}"


def _section(title: str, lines: list[str], limit: int) -> list[str]:
    if not lines:
        return []
    more = [f"… more matches; narrow the query or raise `limit` (max {MAX_LIMIT})"] if len(lines) > limit else []
    return [f"{title}:"] + lines[:limit] + more


def _days(args: dict) -> int | None:
    try:
        return max(1, min(3650, int(args["days"])))
    except (KeyError, TypeError, ValueError):
        return None


def _where(text_columns: str, words: list[str], date_column: str, since: str | None) -> tuple[str, list[str]]:
    """SQL condition for the query words and the time window – either may be missing."""
    where, params = _like(text_columns, words) if words else ("1", [])
    if since:
        where, params = f"{where} AND {date_column} >= ?", params + [since]
    return where, params


def tool_search(cfg: config.Config, con: sqlite3.Connection, args: dict) -> str:
    words = str(args.get("query") or "").split()
    kind, topic, limit, days = args.get("kind", "all"), args.get("topic"), _limit(args, 10), _days(args)
    since = (datetime.now() - timedelta(days=days)).isoformat(timespec="seconds") if days else None
    inbox = topic in ("_inbox", "inbox")
    out: list[str] = []
    if kind in ("all", "files"):
        where, params = _where("name || ' ' || coalesce(summary,'') || ' ' || coalesce(keywords,'')", words,
                               "moved_at", since)
        if inbox:
            where += " AND coalesce(cur_path,'') LIKE '%/_inbox/%'"
        elif topic:
            where, params = where + " AND topic = ? AND coalesce(cur_path,'') NOT LIKE '%/_inbox/%'", params + [topic]
        rows = con.execute(f"SELECT * FROM files WHERE status='moved' AND {where} ORDER BY moved_at DESC LIMIT ?",
                           (*params, limit + 1)).fetchall()
        out += _section("Files", [_file_line(r) for r in rows], limit)
    if kind in ("all", "links") and not inbox:
        where, params = _where("coalesce(title,'') || ' ' || coalesce(domain,'') || ' ' || url", words,
                               "last_visit", since)
        if topic:
            where, params = where + " AND topic = ?", params + [topic]
        rows = con.execute(f"SELECT * FROM links WHERE {where} ORDER BY last_visit DESC LIMIT ?",
                           (*params, limit + 1)).fetchall()
        out += _section("Links", [_link_line(r) for r in rows], limit)
    if kind in ("all", "searches") and not topic:  # web searches have no topic
        where, params = _where("term", words, "at", since)
        rows = con.execute(f"SELECT term, max(at) AS at FROM searches WHERE {where} GROUP BY term ORDER BY at DESC "
                           "LIMIT ?", (*params, limit + 1)).fetchall()
        out += _section("Searched for", [f"{r['at'][:10]} · {r['term']}" for r in rows], limit)
    if out:
        return "\n".join(out)
    what = f" for “{' '.join(words)}”" if words else ""
    return f"No matches{what}" + (f" in the last {days} days." if days else ".")


def tool_recent(cfg: config.Config, con: sqlite3.Connection, args: dict) -> str:
    """The former `recent` tool, for assistants that still have it from an older tool list."""
    return tool_search(cfg, con, {"days": 7, "limit": 20, **args})


def tool_topics(cfg: config.Config, con: sqlite3.Connection, args: dict) -> str:
    files: dict[str, int] = {}
    inbox_count = 0
    for r in con.execute("SELECT topic, cur_path FROM files WHERE status='moved'").fetchall():
        if _is_inbox(r["cur_path"]):
            inbox_count += 1
        elif r["topic"]:
            files[r["topic"]] = files.get(r["topic"], 0) + 1
    links = dict(con.execute("SELECT topic, count(*) FROM links WHERE topic IS NOT NULL GROUP BY topic").fetchall())
    lines = [f"Archive: {_short(str(cfg.root))} (topic folders in {cfg.topics_dir.name}/, "
             f"never open {cfg.private_dir.name}/)"]
    known = {t.slug for t in cfg.topics}
    for t in cfg.topics:
        lines.append(f"{t.slug} · {t.name} — {files.get(t.slug, 0)} files, {links.get(t.slug, 0)} links")
    for slug in sorted((set(files) | set(links)) - known):
        lines.append(f"{slug} · {slug} — {files.get(slug, 0)} files, {links.get(slug, 0)} links")
    if inbox_count:
        lines.append(f"_inbox · Inbox — {inbox_count} files")
    return "\n".join(lines)


HANDLERS = {"search": tool_search, "recent": tool_recent, "topics": tool_topics}


def call_tool(name: str, args: dict, always: bool = False) -> tuple[str, bool]:
    """Run a tool. Returns (text, is_error)."""
    if name not in HANDLERS:
        return f"Unknown tool “{name}”.", True
    cfg = config.load()  # read on every call so switching it off in the app takes effect immediately
    if (reason := refusal(cfg, always)) is not None:
        return reason, True
    con = _db(cfg)
    if con is None:
        return "The archive is still empty – Tidywise has not sorted anything yet.", False
    try:
        return HANDLERS[name](cfg, con, args or {}), False
    except sqlite3.Error as e:
        return f"The archive could not be read: {e}", True
    finally:
        con.close()


# --- The protocol (JSON-RPC 2.0 over stdin/stdout, one message per line) ----------------------------

def handle(message: dict, always: bool = False) -> dict | None:
    """Answer one message. Notifications (no id) get no answer."""
    method, msg_id = message.get("method"), message.get("id")
    if msg_id is None:
        return None

    def result(value: dict) -> dict:
        return {"jsonrpc": "2.0", "id": msg_id, "result": value}

    if method == "initialize":
        wanted = (message.get("params") or {}).get("protocolVersion")
        return result({
            "protocolVersion": wanted if wanted in PROTOCOL_VERSIONS else PROTOCOL_VERSIONS[1],
            "capabilities": {"tools": {}},
            "serverInfo": {"name": "tidywise", "version": __version__},
            "instructions": INSTRUCTIONS,
        })
    if method == "ping":
        return result({})
    if method == "tools/list":
        return result({"tools": TOOLS})
    if method == "tools/call":
        params = message.get("params") or {}
        text, is_error = call_tool(params.get("name", ""), params.get("arguments") or {}, always)
        return result({"content": [{"type": "text", "text": text}], "isError": is_error})
    return {"jsonrpc": "2.0", "id": msg_id, "error": {"code": -32601, "message": f"Method not found: {method}"}}


def serve(always: bool = False, stdin=None, stdout=None) -> None:
    """Serve until the assistant closes the connection."""
    stdin, stdout = stdin or sys.stdin, stdout or sys.stdout
    for line in stdin:
        line = line.strip()
        if not line:
            continue
        try:
            message = json.loads(line)
        except json.JSONDecodeError:
            answer: dict | None = {"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "Parse error"}}
        else:
            answer = handle(message, always) if isinstance(message, dict) else None
        if answer is not None:
            stdout.write(json.dumps(answer, ensure_ascii=False) + "\n")
            stdout.flush()
