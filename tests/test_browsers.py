"""Reading Safari, Firefox and Chromium histories (Arc, Brave, Vivaldi, Edge) – from fake history databases.

    .venv/bin/python -m unittest discover tests
"""
import dataclasses
import sqlite3
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest import mock

from tidywise import browsers, chrome, config
from tidywise.store import Store

SAFARI, FIREFOX, BRAVE = (next(b for b in browsers.BROWSERS if b.id == i) for i in ("safari", "firefox", "brave"))


def make_safari(path: Path, at: datetime) -> None:
    t = SAFARI.from_dt(at)
    con = sqlite3.connect(path)
    con.executescript("""
        CREATE TABLE history_items(id INTEGER PRIMARY KEY, url TEXT, visit_count INTEGER);
        CREATE TABLE history_visits(id INTEGER PRIMARY KEY, history_item INTEGER, visit_time REAL, title TEXT);
    """)
    con.execute("INSERT INTO history_items VALUES(1, 'https://example.com/porto', 2)")
    con.executemany("INSERT INTO history_visits(history_item, visit_time, title) VALUES(1, ?, ?)",
                    [(t, ""), (t + 5, "Porto by tram")])
    con.execute("INSERT INTO history_items VALUES(2, 'https://duckduckgo.com/?q=porto+trams&ia=web', 1)")
    con.execute("INSERT INTO history_visits(history_item, visit_time, title) VALUES(2, ?, 'porto trams at DuckDuckGo')", (t,))
    con.commit()
    con.close()


def make_firefox(root: Path, at: datetime) -> tuple[Path, sqlite3.Connection]:
    profile = root / "Profiles" / "abc.default-release"
    profile.mkdir(parents=True)
    (root / "profiles.ini").write_text(
        "[Profile0]\nName=old\nIsRelative=1\nPath=Profiles/old.default\n\n"
        "[Install4F96D1932A9F858E]\nDefault=Profiles/abc.default-release\nLocked=1\n")
    t = FIREFOX.from_dt(at)
    con = sqlite3.connect(profile / "places.sqlite")
    con.execute("PRAGMA journal_mode=WAL")  # Firefox keeps recent visits in the -wal file
    con.execute("PRAGMA wal_autocheckpoint=0")
    con.executescript("""
        CREATE TABLE moz_places(id INTEGER PRIMARY KEY, url TEXT, title TEXT);
        CREATE TABLE moz_historyvisits(id INTEGER PRIMARY KEY, place_id INTEGER, visit_date INTEGER);
    """)
    con.execute("INSERT INTO moz_places VALUES(1, 'https://example.com/lisbon#top', 'Lisbon in three days')")
    con.execute("INSERT INTO moz_places VALUES(2, 'https://www.google.com/search?q=lisbon+tram+28', 'lisbon tram 28 - Google')")
    con.executemany("INSERT INTO moz_historyvisits(place_id, visit_date) VALUES(?, ?)", [(1, t), (1, t + 10), (2, t)])
    con.commit()
    return profile / "places.sqlite", con  # keep it open: the visits are still only in the -wal file


class BrowsersTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.base = Path(self.dir.name)
        self.at = datetime.now() - timedelta(hours=2)
        self.open = []

    def tearDown(self):
        for con in self.open:
            con.close()
        self.dir.cleanup()

    def cfg(self, *ids: str):
        raw = config.to_dict(config.load(self.base / "missing.toml"))
        raw.update(root=str(self.base / "Tidywise"), links_enabled=True, browsers=list(ids))
        cfg = dataclasses.replace(config._from_raw(raw), state_dir=self.base / "state")
        cfg.ensure_dirs()
        return cfg

    def test_search_terms_from_urls(self):
        for url, term in (("https://www.google.de/search?q=lisbon++sights&hl=de", "lisbon sights"),
                          ("https://duckduckgo.com/?q=porto", "porto"), ("https://html.duckduckgo.com/html/?q=x", "x"),
                          ("https://www.bing.com/search?q=a%20b", "a b"), ("https://search.brave.com/search?q=c", "c"),
                          ("https://developers.google.com/search?q=api", None), ("https://example.com/search?q=x", None),
                          ("https://www.google.com/maps/place/Lisbon", None)):
            self.assertEqual(chrome.search_term(url), term, url)

    def test_firefox_default_profile_and_wal(self):
        root = self.base / "Firefox"
        places, con = make_firefox(root, self.at)
        self.open.append(con)
        with mock.patch.object(browsers, "APP_SUPPORT", self.base):
            self.assertEqual(FIREFOX.history_file(self.cfg("firefox")), places)
        self.assertTrue(places.with_name("places.sqlite-wal").exists())
        visits, terms = FIREFOX.read(places, FIREFOX.from_dt(self.at - timedelta(minutes=1)))
        self.assertIsNone(terms, "Firefox has no search table – terms come from the URLs")
        self.assertEqual(sorted(v[1] for v in visits), ["Lisbon in three days", "lisbon tram 28 - Google"])

    def test_sync_reads_several_browsers_and_keeps_going_when_one_is_blocked(self):
        safari_db = self.base / "History.db"
        make_safari(safari_db, self.at)
        cfg = self.cfg("safari", "brave", "firefox")
        store = Store(cfg.db_path)
        firefox_root = self.base / "Firefox"
        self.open.append(make_firefox(firefox_root, self.at)[1])
        real_read = browsers.Browser.read

        def read(b, path, since):
            if b.id == "brave":
                raise PermissionError(1, "Operation not permitted", str(path))
            return real_read(b, path, since)

        brave_history = self.base / "BraveSoftware/Brave-Browser/Default/History"
        brave_history.parent.mkdir(parents=True)
        brave_history.write_bytes(b"")
        errors: list[str] = []
        with mock.patch.object(browsers, "APP_SUPPORT", self.base), \
                mock.patch.object(browsers.Safari, "history_file", return_value=safari_db), \
                mock.patch.object(browsers.Browser, "read", read), \
                mock.patch.object(chrome, "_classify", side_effect=lambda cfg, items: ["travel"] * len(items)):
            new, searches = chrome.sync(cfg, store, verbose=False, force=True, errors=errors)
            again, _ = chrome.sync(cfg, store, verbose=False, force=True, errors=[])

        self.assertEqual(new, 2)
        self.assertEqual(again, 0, "the read position moved on")
        porto = store.link("https://example.com/porto")
        self.assertEqual((porto["title"], porto["visits"], porto["topic"]), ("Porto by tram", 2, "travel"))
        self.assertEqual(store.link("https://example.com/lisbon")["visits"], 2, "fragment merged, Firefox counted")
        terms = {r[0] for r in store.db.execute("SELECT term FROM searches")}
        self.assertEqual(terms, {"porto trams", "lisbon tram 28"})
        self.assertEqual(searches, 2)
        self.assertEqual(len(errors), 1)
        self.assertIn("Brave history:", errors[0])
        self.assertIn("Operation not permitted", errors[0])
        self.assertEqual(store.get_meta("links_blocked"), "Brave")
        store.db.close()

    def test_nothing_readable_raises_without_an_error_list(self):
        cfg = self.cfg("safari")
        store = Store(cfg.db_path)
        with mock.patch.object(browsers.Safari, "history_file", return_value=self.base / "nope.db"):
            with self.assertRaises(FileNotFoundError):
                chrome.sync(cfg, store, verbose=False, force=True)
        store.db.close()

    def test_chromium_positions_stay_exact(self):
        brave = BRAVE
        position = brave.from_dt(datetime.now())
        self.assertIsInstance(position, int)
        self.assertEqual(brave.position(str(position), datetime.now()), position)
        self.assertEqual(brave.position(str(position + 1), datetime.now()) - position, 1)
        self.assertIsInstance(SAFARI.position(None, datetime.now()), float)

    def test_browser_settings(self):
        self.assertEqual(self.cfg().browsers, ["chrome"], "Chrome, as before, if nothing is chosen")
        self.assertEqual(self.cfg("firefox", "Safari", "safari").browsers, ["safari", "firefox"])
        raw = config.to_dict(config.load(self.base / "missing.toml"))
        raw.update(root=str(self.base / "T"), links_enabled=True)
        for bad in (["netscape"], []):
            with self.assertRaises(config.ConfigError, msg=str(bad)):
                config.save({**raw, "browsers": bad}, self.base / "c.toml")
        config.save({**raw, "browsers": ["safari", "arc"]}, self.base / "c.toml")
        self.assertEqual(config.load(self.base / "c.toml").browsers, ["safari", "arc"])


if __name__ == "__main__":
    unittest.main()
