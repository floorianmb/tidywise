"""The MCP server for AI assistants: protocol, the two tools, and when it refuses to answer.

    .venv/bin/python -m unittest discover tests
"""
import io
import json
import os
import sqlite3
import tempfile
import unittest
from contextlib import closing
from datetime import datetime, timedelta
from pathlib import Path
from unittest import mock

from tidywise import assistants, config, mcp
from tidywise.store import Store, now_iso


class McpTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        base = Path(self.dir.name)
        self.config_path, self.state = base / "config.toml", base / "state"
        raw = config.to_dict(config.load(base / "missing.toml"))
        raw.update(root=str(base / "Tidywise"), mcp_enabled=True)
        config.save(raw, self.config_path)
        patches = [mock.patch.object(config, "CONFIG_PATH", self.config_path),
                   mock.patch.object(config, "STATE_DIR", self.state)]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        self.cfg = config.load()
        store = Store(self.cfg.db_path)
        file_id = store.add_planned(orig_path="/tmp/a.pdf", name="Invoice_hosting.pdf", ext=".pdf", size=1, mtime=1.0,
                                    topic="finance", new_topic=None, summary="Invoice for 100% web_hosting.",
                                    keywords=["invoice", "hosting"], source_url=None, confidence=0.9)
        self.invoice = self.cfg.topics_dir / "finance" / "Invoice_hosting.pdf"
        self.invoice.parent.mkdir(parents=True, exist_ok=True)
        self.invoice.touch()
        store.update_file(file_id, status="moved", moved_at=now_iso(), cur_path=str(self.invoice))
        store.upsert_link("https://example.com/lisbon", "Lisbon in three days", "example.com", "travel", now_iso(), now_iso(), 2)
        store.add_search("lisbon sights", now_iso())
        store.commit()
        store.db.close()

    def tearDown(self):
        self.dir.cleanup()

    def open_app(self):
        mcp.app_pid_path(self.cfg).write_text(str(os.getpid()))

    def call(self, name, **arguments):
        answer = mcp.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                             "params": {"name": name, "arguments": arguments}})
        return answer["result"]["content"][0]["text"], answer["result"]["isError"]

    def test_handshake_and_tool_list(self):
        answer = mcp.handle({"jsonrpc": "2.0", "id": 1, "method": "initialize",
                             "params": {"protocolVersion": "2025-06-18"}})
        self.assertEqual(answer["result"]["protocolVersion"], "2025-06-18")
        self.assertEqual(answer["result"]["serverInfo"]["name"], "tidywise")
        unknown = mcp.handle({"jsonrpc": "2.0", "id": 2, "method": "initialize", "params": {"protocolVersion": "1999-01-01"}})
        self.assertIn(unknown["result"]["protocolVersion"], mcp.PROTOCOL_VERSIONS)
        self.assertIsNone(mcp.handle({"jsonrpc": "2.0", "method": "notifications/initialized"}), "notifications get no answer")
        tools = mcp.handle({"jsonrpc": "2.0", "id": 3, "method": "tools/list"})["result"]["tools"]
        self.assertEqual([t["name"] for t in tools], ["search", "topics"])
        self.assertLess(len(json.dumps(tools)), 1500, "tool definitions cost tokens in every session – keep them short")
        self.assertEqual(mcp.handle({"jsonrpc": "2.0", "id": 4, "method": "nope"})["error"]["code"], -32601)

    def test_answers_only_while_the_app_is_open(self):
        text, is_error = self.call("topics")
        self.assertTrue(is_error)
        self.assertIn("not open", text)
        self.open_app()
        text, is_error = self.call("topics")
        self.assertFalse(is_error)
        self.assertIn("finance · Finance — 1 files", text)

    def test_refuses_when_switched_off_in_the_app(self):
        self.open_app()
        config.update({"mcp_enabled": False}, self.config_path)
        text, is_error = self.call("search", query="invoice")
        self.assertTrue(is_error)
        self.assertIn("not allowed", text)
        self.assertNotIn("Invoice_hosting", text)

    def test_search_files_and_links(self):
        self.open_app()
        text, _ = self.call("search", query="hosting invoice")
        self.assertIn("Invoice_hosting.pdf", text)
        self.assertIn("Topics/finance/Invoice_hosting.pdf", text, "the path lets the assistant open the file itself")
        self.assertNotIn("Links:", text)
        self.assertIn("Lisbon in three days — https://example.com/lisbon (2×)", self.call("search", query="lisbon")[0])
        self.assertIn("No matches", self.call("search", query="lisbon", kind="files")[0])
        self.assertIn("No matches", self.call("search", query="invoice", topic="travel")[0])

    def test_inbox_files_are_separated_from_topic_counts_and_filter(self):
        self.open_app()
        store = Store(self.cfg.db_path)
        fid = store.add_planned(
            orig_path="/tmp/unsure.pdf", name="Unsure_bill.pdf", ext=".pdf", size=1, mtime=1.0,
            topic="finance", new_topic=None, summary="Unclear bill.", keywords=["bill"], source_url=None, confidence=0.2,
        )
        store.update_file(fid, status="moved", moved_at=now_iso(), cur_path=str(self.cfg.inbox_dir / "Unsure_bill.pdf"))
        store.commit()
        store.db.close()

        topics_text, _ = self.call("topics")
        self.assertIn("finance · Finance — 1 files", topics_text)
        self.assertIn("_inbox · Inbox — 1 files", topics_text)
        self.assertIn("No matches", self.call("search", query="Unsure_bill", topic="finance")[0])
        self.assertIn("Unsure_bill.pdf", self.call("search", query="Unsure_bill", topic="_inbox")[0])

    def test_search_treats_wildcards_as_text(self):
        self.open_app()
        self.assertIn("Invoice_hosting.pdf", self.call("search", query="100%")[0])
        self.assertIn("Invoice_hosting.pdf", self.call("search", query="web_hosting")[0])
        self.assertIn("No matches", self.call("search", query="web%hosting")[0])

    def test_search_without_query_lists_the_newest(self):
        self.open_app()
        store = Store(self.cfg.db_path)
        month_ago = (datetime.now() - timedelta(days=30)).isoformat(timespec="seconds")
        store.upsert_link("https://example.com/porto", "Porto by tram", "example.com", "travel", month_ago, month_ago, 1)
        store.add_search("porto trams", month_ago)
        store.commit()
        store.db.close()

        text, _ = self.call("search")
        for part in ("Files:", "Invoice_hosting.pdf", "Links:", "Lisbon", "Porto", "Searched for:", "porto trams"):
            self.assertIn(part, text)
        week, _ = self.call("search", days=7)
        for part in ("Invoice_hosting.pdf", "Lisbon", "lisbon sights"):
            self.assertIn(part, week)
        self.assertNotIn("orto", week, "older than the window")
        self.assertNotIn("Links:", self.call("search", days=7, kind="files")[0])
        self.assertEqual(self.call("search", query="porto", days=7)[0], "No matches for “porto” in the last 7 days.")
        self.assertEqual(self.call("search", query="porto", kind="searches")[0], "Searched for:\n" + month_ago[:10] + " · porto trams")
        self.assertNotIn("Searched for:", self.call("search", query="lisbon", topic="travel")[0], "searches have no topic")

    def test_former_recent_tool_still_answers(self):
        self.open_app()
        text, is_error = self.call("recent", kind="links")
        self.assertFalse(is_error)
        self.assertIn("Lisbon in three days", text)

    def test_search_flags_files_moved_or_deleted_outside_tidywise(self):
        self.invoice.rename(self.invoice.with_suffix(".moved"))
        with closing(sqlite3.connect(self.cfg.db_path.as_uri() + "?mode=ro", uri=True)) as con:
            con.row_factory = sqlite3.Row
            for args in ({"query": "invoice"}, {"days": 7, "kind": "files"}):
                with self.subTest(args=args):
                    text = mcp.tool_search(self.cfg, con, args)
                    self.assertIn("Invoice_hosting.pdf", text)
                    self.assertIn("file unavailable at its indexed location", text)
                    self.assertNotIn(" → ", text)

    def test_serve_reads_and_writes_one_message_per_line(self):
        requests = "\n".join(json.dumps(m) for m in (
            {"jsonrpc": "2.0", "id": 1, "method": "ping"},
            {"jsonrpc": "2.0", "method": "notifications/initialized"},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": "topics"}},
        )) + "\nnot json\n"
        out = io.StringIO()
        mcp.serve(always=True, stdin=io.StringIO(requests), stdout=out)
        answers = [json.loads(line) for line in out.getvalue().splitlines()]
        self.assertEqual([a.get("id") for a in answers], [1, 2, None])
        self.assertFalse(answers[1]["result"]["isError"], "--always answers without the app")
        self.assertEqual(answers[2]["error"]["code"], -32700)


class AssistantsTest(unittest.TestCase):
    def test_manual_snippet_uses_the_common_format(self):
        with mock.patch.dict(os.environ, {"TIDYWISE_CLI": "/bin/echo"}):
            snippet = json.loads(assistants.manual_snippet())
        self.assertEqual(snippet, {"mcpServers": {"tidywise": {"command": "/bin/echo", "args": ["mcp"]}}})

    def test_unknown_assistant(self):
        with self.assertRaises(assistants.AssistantError):
            assistants.connect("nope")

    def test_opencode_jsonc_ignores_comments_and_names_actual_file(self):
        with tempfile.TemporaryDirectory() as td:
            json_path = Path(td) / "opencode.json"
            jsonc_path = Path(td) / "opencode.jsonc"
            oc = assistants.OpenCode("opencode", "OpenCode", "opencode")
            oc.configs = [json_path, jsonc_path]
            jsonc_path.write_text('// "tidywise" commented out\n{"mcp": {}}\n')
            self.assertFalse(oc.connected())
            jsonc_path.write_text('// active\n{"mcp": {"tidywise": {"command": ["tidywise", "mcp"]}}}\n')
            self.assertTrue(oc.connected())
            with self.assertRaises(assistants.AssistantError) as ctx:
                oc.remove("/usr/bin/true")
            self.assertIn("opencode.jsonc", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
