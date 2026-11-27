"""Tests for file scanning, planning, moving, re-topicing, Chrome normalization, extraction and rendering.

    python3 -m unittest discover tests
"""
from __future__ import annotations

import argparse
import contextlib
import dataclasses
import io
import json
import sqlite3
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest import mock

from tidywise import __main__ as cli
from tidywise import chrome, config, extract, organize, render
from tidywise.store import Store, now_iso


class OrganizeTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        base = Path(self.dir.name)
        self.watch = base / "Downloads"
        self.watch.mkdir()
        raw = config.to_dict(config.load(base / "missing.toml"))
        raw.update(root=str(base / "Tidywise"), watch=[str(self.watch)], min_age_minutes=0)
        self.cfg = dataclasses.replace(config._from_raw(raw), state_dir=base / "state")
        self.cfg.ensure_dirs()
        self.store = Store(self.cfg.db_path)

    def tearDown(self):
        self.store.db.close()
        self.dir.cleanup()

    def test_plan_commits_dropped_vanished_files_without_new_files(self):
        ghost = self.watch / "deleted.pdf"
        self.store.add_planned(
            orig_path=str(ghost), name="deleted.pdf", ext=".pdf", size=10, mtime=1.0,
            topic="finance", new_topic=None, summary="Gone", keywords=[], source_url=None, confidence=0.9,
        )
        self.store.commit()
        organize.plan(self.cfg, self.store, verbose=False)
        # Re-open DB from a fresh connection to confirm the deletion was actually committed.
        fresh = Store(self.cfg.db_path)
        self.assertEqual(fresh.planned(), [])
        fresh.db.close()

    def test_apply_commits_dropped_vanished_file(self):
        ghost = self.watch / "gone.pdf"
        self.store.add_planned(
            orig_path=str(ghost), name="gone.pdf", ext=".pdf", size=10, mtime=1.0,
            topic="finance", new_topic=None, summary="Gone", keywords=[], source_url=None, confidence=0.9,
        )
        self.store.commit()
        done = organize.apply(self.cfg, self.store)
        self.assertEqual(done, [])
        fresh = Store(self.cfg.db_path)
        self.assertEqual(fresh.planned(), [])
        fresh.db.close()

    def test_permission_error_keeps_existing_planned_suggestions(self):
        kept = self.watch / "bill.pdf"
        self.store.add_planned(
            orig_path=str(kept), name="bill.pdf", ext=".pdf", size=10, mtime=1.0,
            topic="finance", new_topic=None, summary="Bill", keywords=[], source_url=None, confidence=0.9,
        )
        self.store.commit()
        with mock.patch.object(Path, "iterdir", side_effect=PermissionError("Operation not permitted")):
            rows = organize.plan(self.cfg, self.store, verbose=False)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["name"], "bill.pdf")

    def test_plan_survives_file_vanishing_mid_scan(self):
        a = self.watch / "a.txt"
        b = self.watch / "b.txt"
        a.write_text("alpha")
        b.write_text("beta")
        calls = []

        def fake_classify(cfg, p):
            calls.append(p.name)
            if p.name == "a.txt":
                a.unlink()
                raise FileNotFoundError(str(a))
            return {"topic": "work", "summary": "Beta file", "keywords": ["beta"], "confidence": 0.9, "source_url": None}

        with mock.patch.object(organize, "classify", side_effect=fake_classify):
            rows = organize.plan(self.cfg, self.store, verbose=False)
        self.assertEqual(calls, ["a.txt", "b.txt"])
        self.assertEqual([r["name"] for r in rows], ["b.txt"])

    def test_plan_stores_redacted_summary_and_keywords(self):
        (self.watch / "statement.pdf").write_text("pdf")
        res = {"topic": "finance", "summary": "Bank statement for IBAN DE89 3704 0044 0532 0130 00 from 03/2024",
               "keywords": ["bank statement", "DE89370400440532013000", "card 4111 1111 1111 1111"],
               "confidence": 0.9, "source_url": None}
        with mock.patch.object(organize, "classify", return_value=res):
            row = organize.plan(self.cfg, self.store, verbose=False)[0]
        self.assertEqual(row["summary"], "Bank statement for IBAN [redacted] from 03/2024")
        self.assertEqual(row["keywords"], "bank statement, card [redacted]")

    def test_set_topic_same_folder_does_not_rename_and_missing_or_undone_fail_cleanly(self):
        f = self.watch / "invoice.pdf"
        f.write_text("pdf")
        fid = self.store.add_planned(
            orig_path=str(f), name="invoice.pdf", ext=".pdf", size=3, mtime=f.stat().st_mtime,
            topic="finance", new_topic=None, summary="Invoice", keywords=[], source_url=None, confidence=0.95,
        )
        self.store.commit()
        organize.apply(self.cfg, self.store, ids=[fid])
        moved_path = Path(self.store.get_file(fid)["cur_path"])
        self.assertEqual(moved_path.name, "invoice.pdf")

        # Setting the same topic again must not rename invoice.pdf -> invoice (2).pdf
        organize.set_topic(self.cfg, self.store, fid, "finance")
        self.assertTrue(moved_path.exists())
        self.assertEqual(Path(self.store.get_file(fid)["cur_path"]), moved_path)

        # Missing file on disk raises SystemExit, not FileNotFoundError
        moved_path.unlink()
        with self.assertRaises(SystemExit):
            organize.set_topic(self.cfg, self.store, fid, "work")

        # Undone file raises SystemExit instead of silently succeeding
        self.store.update_file(fid, status="undone")
        self.store.commit()
        with self.assertRaises(SystemExit):
            organize.set_topic(self.cfg, self.store, fid, "work")


class RedactTest(unittest.TestCase):
    def test_redacts_ibans_and_long_numbers(self):
        cases = {
            "IBAN DE89 3704 0044 0532 0130 00": "IBAN [redacted]",
            "IBAN DE89370400440532013000.": "IBAN [redacted].",
            "GB29 NWBK 6016 1331 9268 19 (Barclays)": "[redacted] (Barclays)",
            "NO93 8601 1117 947": "[redacted]",
            "IBAN DE89 3704 0044 0532 0130 00 BICX": "IBAN [redacted] BICX",  # stops where the valid IBAN ends
            "Card 4111 1111 1111 1111": "Card [redacted]",
            "Card 4111-1111-1111-1111": "Card [redacted]",
            "Tax ID 12 345 678 901": "Tax ID [redacted]",
            "Tax number 123/456/78901": "Tax number [redacted]",
            "SSN 123-45-6789": "SSN [redacted]",
            "Account 1234567890, closed": "Account [redacted], closed",
            "Phone +49 30 12345678": "Phone [redacted]",
        }
        for text, expected in cases.items():
            with self.subTest(text=text):
                self.assertEqual(organize.redact(text), expected)

    def test_keeps_dates_years_amounts_and_short_numbers(self):
        for text in ("Invoice RE-2024-00123 from 15.03.2024 over 1.250,00 EUR",
                     "Tax returns 2022 2023 2024", "Period 2024-01-01 to 2024-12-31",
                     "Contract 2019-2024, 12 pages", "Amount 1,234,567.89 USD", "Amount 123456789.50",
                     "iPhone 15 Pro 256 GB", "ISO 9001 2015 certificate", "Order 12345678",
                     "DE89 3704 is too short for an IBAN"):
            with self.subTest(text=text):
                self.assertEqual(organize.redact(text), text)

    def test_keywords_that_were_only_a_number_are_dropped(self):
        self.assertEqual(organize.redact_keywords(["invoice", "4111 1111 1111 1111", "account 1234567890", 2024]),
                         ["invoice", "account [redacted]", "2024"])


class ExtractAndChromeTest(unittest.TestCase):
    def test_textutil_passes_noload_and_excludes_pages(self):
        self.assertNotIn(".pages", extract.TEXTUTIL_EXT)
        fake = mock.Mock(returncode=0, stdout=b"Hello world")
        with mock.patch.object(extract, "_run", return_value=fake) as run:
            out = extract.text_excerpt(Path("/tmp/saved.html"))
        self.assertEqual(out, "Hello world")
        cmd = run.call_args[0][0]
        self.assertEqual(cmd[:3], ["textutil", "-noload", "-convert"])

    def test_chrome_normalize_handles_google_sorry_and_docs(self):
        self.assertIsNone(chrome._normalize("https://www.google.com/sorry/index?continue=x"))
        self.assertIsNone(chrome._normalize("https://www.google.com/search?q=tidywise"))
        self.assertEqual(
            chrome._normalize("https://developers.google.com/search/docs/fundamentals"),
            "https://developers.google.com/search/docs/fundamentals",
        )
        self.assertEqual(
            chrome._normalize("https://maps.google.com/maps/place/Lisbon/@38.7,-9.1,12z"),
            "https://maps.google.com/maps/place/Lisbon",
        )
        for search in ("https://www.google.co.uk/search?q=x", "https://images.google.com/search?q=x",
                       "https://cn.bing.com/search?q=x", "https://www.bing.com/search?q=x"):
            self.assertIsNone(chrome._normalize(search), search)


class ChromeSyncTest(unittest.TestCase):
    """An interrupted sync neither loses visits nor counts them twice; untagged links are caught up later."""

    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        base = Path(self.dir.name)
        raw = config.to_dict(config.load(base / "missing.toml"))
        raw.update(root=str(base / "Tidywise"), watch=[str(base / "Downloads")], links_enabled=True)
        self.cfg = dataclasses.replace(config._from_raw(raw), state_dir=base / "state")
        self.cfg.ensure_dirs()
        self.store = Store(self.cfg.db_path)
        self.history = base / "History"
        con = sqlite3.connect(self.history)
        con.executescript("""
            CREATE TABLE urls(id INTEGER PRIMARY KEY, url TEXT, title TEXT, last_visit_time INTEGER);
            CREATE TABLE visits(id INTEGER PRIMARY KEY, url INTEGER, visit_time INTEGER);
            CREATE TABLE keyword_search_terms(term TEXT, url_id INTEGER);
        """)
        at = chrome.dt_to_chrome(datetime.now() - timedelta(hours=1))
        con.execute("INSERT INTO urls VALUES(1, 'https://example.com/lisbon', 'Trip to Lisbon', ?)", (at,))
        con.executemany("INSERT INTO visits(url, visit_time) VALUES(1, ?)", [(at,), (at + 1,)])
        con.execute("INSERT INTO urls VALUES(2, 'https://www.google.com/search?q=lisbon', 'lisbon', ?)", (at,))
        con.execute("INSERT INTO keyword_search_terms VALUES('lisbon', 2)")
        con.commit()
        con.close()

    def tearDown(self):
        self.store.db.close()
        self.dir.cleanup()

    def test_interrupted_sync_keeps_visits_once_and_tags_later(self):
        with mock.patch.object(chrome, "history_path", return_value=self.history):
            with mock.patch.object(chrome, "_classify", side_effect=KeyboardInterrupt):
                with self.assertRaises(KeyboardInterrupt):
                    chrome.sync(self.cfg, self.store, verbose=False, force=True)
            link = self.store.link("https://example.com/lisbon")
            self.assertEqual((link["visits"], link["topic"]), (2, None))
            with mock.patch.object(chrome, "_classify", side_effect=lambda cfg, items: ["travel"] * len(items)):
                new, _ = chrome.sync(self.cfg, self.store, verbose=False, force=True)
        self.assertEqual(new, 0)
        link = self.store.link("https://example.com/lisbon")
        self.assertEqual((link["visits"], link["topic"]), (2, "travel"))
        self.assertEqual(self.store.db.execute("SELECT count(*) FROM searches").fetchone()[0], 1)


class McpAddTest(unittest.TestCase):
    def run_add(self, config_path: Path) -> str:
        out = io.StringIO()
        with mock.patch.object(config, "CONFIG_PATH", config_path), \
                mock.patch.object(cli.assistants, "connect", return_value="Connected to Claude Code."), \
                mock.patch.object(cli.assistants, "status", return_value={"clients": []}), \
                contextlib.redirect_stdout(out):
            cli.cmd_mcp(argparse.Namespace(action="add", client="claude", as_json=False, always=False))
        return out.getvalue()

    def test_add_before_setup_does_not_create_a_config(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "config.toml"
            self.assertIn("isn't set up yet", self.run_add(path))
            self.assertFalse(path.exists())  # otherwise the app would skip its setup

    def test_add_after_setup_switches_searching_on(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "config.toml"
            config.save(config.to_dict(config.load(path)), path)
            self.run_add(path)
            self.assertTrue(config.load(path).mcp_enabled)


class ConfigSaveTest(unittest.TestCase):
    def test_save_from_an_older_app_keeps_settings_it_does_not_know(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "config.toml"
            raw = {**config.to_dict(config.load(path)), "root": td + "/Tidywise", "llm_backend": "openai",
                   "llm_url": "http://127.0.0.1:8080/v1", "model": "local-model"}
            config.save(raw, path)
            from_app = {k: v for k, v in raw.items() if not k.startswith("llm_")} | {"profile": "Gardener"}
            with mock.patch.object(config, "CONFIG_PATH", path), contextlib.redirect_stdout(io.StringIO()):
                cli.cmd_config(argparse.Namespace(action="save", json=json.dumps(from_app), as_json=True, rest=[]))
            cfg = config.load(path)
            self.assertEqual((cfg.llm_backend, cfg.llm_url, cfg.profile), ("openai", "http://127.0.0.1:8080/v1", "Gardener"))


class RenderTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        base = Path(self.dir.name)
        raw = config.to_dict(config.load(base / "missing.toml"))
        raw.update(root=str(base / "Tidywise"), watch=[str(base / "Downloads")])
        self.cfg = dataclasses.replace(config._from_raw(raw), state_dir=base / "state")
        self.cfg.ensure_dirs()
        self.store = Store(self.cfg.db_path)

    def tearDown(self):
        self.store.db.close()
        self.dir.cleanup()

    def test_inbox_collision_escaping_deleted_topic_and_stale_cleanup(self):
        # Collision in _inbox: display name is report [1].pdf, actual file is report [1] (2).pdf
        inbox_file = self.cfg.inbox_dir / "report [1] (2).pdf"
        inbox_file.write_text("x")
        fid = self.store.add_planned(
            orig_path="/tmp/report [1].pdf", name="report [1].pdf", ext=".pdf", size=1, mtime=1.0,
            topic="other", new_topic=None, summary="Note | test", keywords=[], source_url=None, confidence=0.2,
        )
        self.store.update_file(fid, status="moved", moved_at=now_iso(), cur_path=str(inbox_file))

        # File in a removed topic still appears in AGENTS.md & keeps its _index.md
        old_file = self.cfg.topics_dir / "legacy-topic" / "doc.pdf"
        old_file.parent.mkdir(parents=True, exist_ok=True)
        old_file.write_text("x")
        fid2 = self.store.add_planned(
            orig_path="/tmp/doc.pdf", name="doc.pdf", ext=".pdf", size=1, mtime=1.0,
            topic="legacy-topic", new_topic=None, summary="Legacy", keywords=[],
            source_url="https://example.com/a (1)", confidence=0.9,
        )
        self.store.update_file(fid2, status="moved", moved_at=now_iso(), cur_path=str(old_file))

        # Stale generated markdown files that should be cleaned up – but never the user's own notes
        stale_link = self.cfg.links_dir / "removed.md"
        stale_link.write_text("# Links: Removed\n")
        stale_day = self.cfg.days_dir / "2020-01-01.md"
        stale_day.write_text("# 2020-01-01\n")
        own_notes = [self.cfg.links_dir / "my-notes.md", self.cfg.days_dir / "2020-01-02.md"]
        for note in own_notes:
            note.write_text("My own notes\n")
        self.store.commit()

        render.render(self.cfg, self.store)

        inbox_md = (self.cfg.inbox_dir / "_index.md").read_text()
        self.assertIn(r"[report \[1\].pdf](report%20%5B1%5D%20%282%29.pdf)", inbox_md)

        legacy_md = (self.cfg.topics_dir / "legacy-topic" / "_index.md").read_text()
        self.assertIn("https://example.com/a%20%281%29", legacy_md)

        agents_md = (self.cfg.root / "AGENTS.md").read_text()
        self.assertIn("legacy-topic", agents_md)
        self.assertFalse(stale_link.exists())
        self.assertFalse(stale_day.exists())
        for note in own_notes:
            self.assertTrue(note.exists(), note)


if __name__ == "__main__":
    unittest.main()
