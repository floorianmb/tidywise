"""Where the browsers on a Mac keep their history, and how to read it.

Every browser is opt-in (`browsers` in the settings) and read from a copy of its database in a temporary
folder – the browser keeps the original locked, and nothing is ever written to it or sent anywhere.
Safari's history is protected by macOS: reading it needs Full Disk Access, just like a Chrome profile can.
"""
from __future__ import annotations

import configparser
import shutil
import sqlite3
import tempfile
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .config import BROWSER_IDS, Config

APP_SUPPORT = Path.home() / "Library/Application Support"
CHROME_EPOCH = datetime(1601, 1, 1, tzinfo=timezone.utc)   # Chromium: microseconds since 1601
SAFARI_EPOCH = datetime(2001, 1, 1, tzinfo=timezone.utc)   # Safari (Core Data): seconds since 2001
UNIX_EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)     # Firefox: microseconds since 1970

# (url, title, first visit, last visit, visits) – times in the browser's own unit
Visit = tuple[str, str, float, float, int]


@dataclass(frozen=True)
class Browser:
    id: str
    title: str
    epoch: datetime
    per_second: float  # time units per second

    def history_file(self, cfg: Config) -> Path | None:
        raise NotImplementedError

    def to_dt(self, value: float) -> datetime:
        return (self.epoch + timedelta(seconds=value / self.per_second)).astimezone()

    def from_dt(self, dt: datetime) -> float:
        value = (dt.astimezone(timezone.utc) - self.epoch).total_seconds() * self.per_second
        return int(value) if self.per_second >= 1000 else value  # whole microseconds stay exact as int

    def position(self, text: str | None, default: datetime) -> float:
        """The stored read position – an int for microsecond clocks, which a float can't hold exactly."""
        if not text:
            return self.from_dt(default)
        try:
            return int(text)
        except ValueError:
            return float(text)

    def visits(self, con: sqlite3.Connection, since: float) -> list[Visit]:
        raise NotImplementedError

    def searches(self, con: sqlite3.Connection, since: float) -> list[tuple[str, float]] | None:
        """Search terms the browser records itself – None if it doesn't (then they come from the URLs)."""
        return None

    def read(self, path: Path, since: float) -> tuple[list[Visit], list[tuple[str, float]] | None]:
        """Visits and searches after `since`, read from a copy. Raises OSError (e.g. PermissionError without
        Full Disk Access) or sqlite3.Error."""
        with tempfile.TemporaryDirectory() as td:
            copy = Path(td) / path.name
            shutil.copy2(path, copy)
            for suffix in ("-wal", "-journal"):  # changes not yet written into the main file
                side = path.with_name(path.name + suffix)
                if side.exists():
                    shutil.copy2(side, copy.with_name(copy.name + suffix))
            con = sqlite3.connect(copy)  # our own copy: may be written to, so the WAL can be applied
            try:
                return self.visits(con, since), self.searches(con, since)
            finally:
                con.close()


@dataclass(frozen=True)
class Chromium(Browser):
    folder: str = ""  # under ~/Library/Application Support

    def history_file(self, cfg: Config) -> Path | None:
        profile = cfg.chrome_profile if self.id == "chrome" else "Default"
        return APP_SUPPORT / self.folder / profile / "History"

    def visits(self, con: sqlite3.Connection, since: float) -> list[Visit]:
        return con.execute(
            """SELECT u.url, u.title, MIN(v.visit_time), MAX(v.visit_time), COUNT(*)
               FROM visits v JOIN urls u ON u.id = v.url
               WHERE v.visit_time > ? GROUP BY u.id ORDER BY MAX(v.visit_time)""",
            (since,),
        ).fetchall()

    def searches(self, con: sqlite3.Connection, since: float) -> list[tuple[str, float]]:
        return con.execute(
            """SELECT k.term, u.last_visit_time FROM keyword_search_terms k JOIN urls u ON u.id = k.url_id
               WHERE u.last_visit_time > ?""",
            (since,),
        ).fetchall()


@dataclass(frozen=True)
class Safari(Browser):
    def history_file(self, cfg: Config) -> Path | None:
        return Path.home() / "Library/Safari/History.db"

    def visits(self, con: sqlite3.Connection, since: float) -> list[Visit]:
        # Safari keeps the title per visit; take the latest one that isn't empty.
        return con.execute(
            """SELECT i.url,
                      (SELECT t.title FROM history_visits t WHERE t.history_item = i.id AND coalesce(t.title, '') != ''
                       ORDER BY t.visit_time DESC LIMIT 1),
                      MIN(v.visit_time), MAX(v.visit_time), COUNT(*)
               FROM history_visits v JOIN history_items i ON i.id = v.history_item
               WHERE v.visit_time > ? GROUP BY i.id ORDER BY MAX(v.visit_time)""",
            (since,),
        ).fetchall()


@dataclass(frozen=True)
class Firefox(Browser):
    def history_file(self, cfg: Config) -> Path | None:
        root = APP_SUPPORT / "Firefox"
        profile = _firefox_default_profile(root)
        if profile is not None:
            return profile / "places.sqlite"
        candidates = sorted((root / "Profiles").glob("*/places.sqlite"), key=lambda p: p.stat().st_mtime, reverse=True)
        return candidates[0] if candidates else root / "Profiles" / "places.sqlite"

    def visits(self, con: sqlite3.Connection, since: float) -> list[Visit]:
        return con.execute(
            """SELECT p.url, p.title, MIN(v.visit_date), MAX(v.visit_date), COUNT(*)
               FROM moz_historyvisits v JOIN moz_places p ON p.id = v.place_id
               WHERE v.visit_date > ? GROUP BY p.id ORDER BY MAX(v.visit_date)""",
            (since,),
        ).fetchall()


def _firefox_default_profile(root: Path) -> Path | None:
    """The profile Firefox opens by default, from profiles.ini."""
    ini = configparser.ConfigParser(interpolation=None)
    try:
        ini.read(root / "profiles.ini", encoding="utf-8")
    except (configparser.Error, OSError, UnicodeDecodeError):
        return None

    def resolve(path: str, relative: bool = True) -> Path:
        return root / path if relative else Path(path)

    for section in ini.sections():  # newer Firefox: [Install…] Default=Profiles/xyz.default-release
        if section.startswith("Install") and ini.get(section, "Default", fallback=""):
            return resolve(ini.get(section, "Default"))
    profiles = [s for s in ini.sections() if s.startswith("Profile") and ini.get(s, "Path", fallback="")]
    for s in sorted(profiles, key=lambda s: ini.get(s, "Default", fallback="0") != "1"):
        return resolve(ini.get(s, "Path"), ini.get(s, "IsRelative", fallback="1") == "1")
    return None


MICRO = 1_000_000
BROWSERS: list[Browser] = [
    Chromium("chrome", "Chrome", CHROME_EPOCH, MICRO, "Google/Chrome"),
    Safari("safari", "Safari", SAFARI_EPOCH, 1),
    Firefox("firefox", "Firefox", UNIX_EPOCH, MICRO),
    Chromium("arc", "Arc", CHROME_EPOCH, MICRO, "Arc/User Data"),
    Chromium("brave", "Brave", CHROME_EPOCH, MICRO, "BraveSoftware/Brave-Browser"),
    Chromium("vivaldi", "Vivaldi", CHROME_EPOCH, MICRO, "Vivaldi"),
    Chromium("edge", "Edge", CHROME_EPOCH, MICRO, "Microsoft Edge"),
]
assert tuple(b.id for b in BROWSERS) == BROWSER_IDS


def selected(cfg: Config) -> list[Browser]:
    return [b for b in BROWSERS if b.id in cfg.browsers]
