"""Read, validate and write the settings.

`~/.config/tidywise/config.toml` is the single source of truth. The Mac app reads it through
`tidywise config --as-json` and writes through `tidywise config save`, so exactly one piece of code knows the file format.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

CONFIG_PATH = Path(os.environ.get("TIDYWISE_CONFIG", "~/.config/tidywise/config.toml")).expanduser()
DEFAULT_CONFIG = Path(__file__).with_name("default_config.toml")
# Database, status and move log live outside ~/Downloads so the Mac app can read them without folder
# permission and launchd doesn't trip over macOS privacy protection.
STATE_DIR = Path(os.environ.get("TIDYWISE_STATE_DIR", "~/Library/Application Support/tidywise")).expanduser()

FALLBACK = "other"
# Installs set up before the switch to English keep their names: the catch-all topic "sonstiges"
# and the folders Themen/, Tage/, _privat/. Nothing is renamed on disk.
LEGACY_FALLBACK = "sonstiges"
RESERVED_SLUGS = {"new", "neu", "_inbox", "_private", "_privat"}
SUBFOLDERS = ("none", "year", "month")
# How Tidywise talks to the local model: Ollama's own API, or the OpenAI-style API that LM Studio,
# mlx-lm (`mlx_lm.server`) and llama.cpp (`llama-server`) offer.
LLM_BACKENDS = ("ollama", "openai")
# Browsers whose history can be read as a link collection (details in browsers.py).
BROWSER_IDS = ("chrome", "safari", "firefox", "arc", "brave", "vivaldi", "edge")
# Icons and colours for the topic slugs older (German) configurations use, so they keep their look.
LEGACY_STYLES = {
    "persoenliche-dokumente": ("person.text.rectangle.fill", "orange"), "finanzen": ("eurosign.circle.fill", "green"),
    "arbeit": ("briefcase.fill", "cyan"), "studium": ("graduationcap.fill", "blue"),
    "ki-und-entwicklung": ("chevron.left.forwardslash.chevron.right", "purple"), "software": ("shippingbox.fill", "teal"),
    "fotos-und-medien": ("photo.fill", "yellow"), "musik-audio": ("music.note", "pink"),
    "games": ("gamecontroller.fill", "red"), "shopping": ("cart.fill", "brown"), "reisen": ("airplane", "mint"),
    "social-und-unterhaltung": ("bubble.left.and.bubble.right.fill", "magenta"),
    "alltag-und-konten": ("key.fill", "slate"), "sonstiges": ("tray.fill", "gray"),
}
# Colour names the app knows. Order = order in the colour picker.
COLORS = ("blue", "cyan", "teal", "mint", "green", "yellow", "orange", "red", "pink", "magenta", "purple",
          "indigo", "brown", "slate", "gray")


class ConfigError(ValueError):
    """A setting is invalid. The text is written for people and shown in the app."""


@dataclass(frozen=True)
class Topic:
    slug: str
    name: str
    description: str = ""
    symbol: str = ""   # SF Symbol for the app, e.g. "airplane"
    color: str = ""    # one of the names in COLORS


@dataclass(frozen=True)
class Config:
    root: Path
    watch: list[Path]
    model: str
    ollama_url: str
    llm_backend: str
    llm_url: str
    allow_remote_llm: bool
    profile: str
    summary_language: str
    min_age_minutes: float
    min_confidence: float
    auto_apply: bool
    subfolders: str
    interval_minutes: int
    links_enabled: bool
    browsers: list[str]
    chrome_profile: str
    links_first_days: int
    links_interval_minutes: int
    mcp_enabled: bool
    skip_extensions: list[str]
    topics: list[Topic]
    state_dir: Path = field(default_factory=lambda: STATE_DIR)

    @property
    def llm_endpoint(self) -> str:
        """The address of the model server in use."""
        return self.ollama_url if self.llm_backend == "ollama" else self.llm_url

    @property
    def db_path(self) -> Path:
        return self.state_dir / "tidywise.sqlite"

    def _dir(self, name: str, legacy: str | None = None) -> Path:
        """A folder inside the destination. An existing folder with the old German name is kept."""
        if legacy and (self.root / legacy).is_dir() and not (self.root / name).exists():
            return self.root / legacy
        return self.root / name

    @property
    def topics_dir(self) -> Path:
        return self._dir("Topics", "Themen")

    @property
    def links_dir(self) -> Path:
        return self._dir("Links")

    @property
    def days_dir(self) -> Path:
        return self._dir("Days", "Tage")

    @property
    def inbox_dir(self) -> Path:
        return self._dir("_inbox")

    @property
    def private_dir(self) -> Path:
        return self._dir("_private", "_privat")

    @property
    def slugs(self) -> list[str]:
        return [t.slug for t in self.topics]

    @property
    def fallback(self) -> str:
        """Slug of the catch-all topic."""
        return LEGACY_FALLBACK if LEGACY_FALLBACK in self.slugs and FALLBACK not in self.slugs else FALLBACK

    def topic(self, slug: str) -> Topic | None:
        return next((t for t in self.topics if t.slug == slug), None)

    def ensure_dirs(self) -> None:
        for d in (self.state_dir, self.topics_dir, self.links_dir, self.days_dir, self.inbox_dir, self.private_dir):
            d.mkdir(parents=True, exist_ok=True)
        self._migrate_old_state()

    def _migrate_old_state(self) -> None:
        """State used to live in <root>/.state – move it once."""
        old = self.root / ".state"
        if not old.is_dir() or self.db_path.exists():
            return
        for f in old.iterdir():
            if f.name not in ("agent.lock", ".DS_Store"):
                shutil.move(str(f), str(self.state_dir / f.name))
        for f in old.iterdir():
            if f.is_file() or f.is_symlink():
                f.unlink()
        try:
            old.rmdir()
        except OSError:
            pass


# --- Topics ---------------------------------------------------------------------------------------

def slugify(name: str) -> str:
    """"Travel & Transport" → "travel-transport". The slug is the topic's folder name."""
    s = name.strip().lower()
    for a, b in (("ä", "ae"), ("ö", "oe"), ("ü", "ue"), ("ß", "ss")):
        s = s.replace(a, b)
    s = re.sub(r"[^a-z0-9]+", "-", s).strip("-")
    return s[:40].strip("-") or "topic"


def _auto_color(slug: str) -> str:
    """Topics without a chosen colour get a fixed one that depends only on the name."""
    palette = [c for c in COLORS if c != "gray"]
    return palette[sum(slug.encode()) % len(palette)]


def _default_styles() -> dict[str, tuple[str, str]]:
    with open(DEFAULT_CONFIG, "rb") as f:
        raw = tomllib.load(f)
    return LEGACY_STYLES | {t["slug"]: (t.get("symbol", ""), t.get("color", "")) for t in raw.get("topics", [])}


def _topics(raw_topics: list, *, strict: bool) -> list[Topic]:
    """Build the topic list. `strict` (when saving) reports errors; otherwise problems are repaired silently."""
    styles = _default_styles()
    topics: list[Topic] = []
    seen: set[str] = set()
    for t in raw_topics or []:
        name = str(t.get("name") or "").strip()
        slug = str(t.get("slug") or "").strip().lower()
        if not slug and not name:
            continue
        if not slug:  # new topic without a folder name: derive it from the name
            base = slug = slugify(name)
            n = 2
            while slug in seen or slug in RESERVED_SLUGS:
                slug = f"{base}-{n}"
                n += 1
        elif not re.fullmatch(r"[a-z0-9][a-z0-9-]*", slug) or slug in RESERVED_SLUGS:
            if strict:
                raise ConfigError(f"“{slug}” can't be a folder name. Use lowercase letters, digits and hyphens.")
            continue
        if slug in seen:
            if strict:
                raise ConfigError(f"The topic “{slug}” exists twice.")
            continue
        seen.add(slug)
        symbol, color = styles.get(slug, ("", ""))
        color = str(t.get("color") or color or _auto_color(slug))
        topics.append(Topic(
            slug=slug, name=name or slug, description=str(t.get("description") or "").strip(),
            symbol=str(t.get("symbol") or symbol or "folder.fill"),
            color=color if color in COLORS else _auto_color(slug),
        ))
    if FALLBACK not in seen and LEGACY_FALLBACK not in seen:  # without a catch-all the model can't say "none of these"
        topics.append(Topic(FALLBACK, "Other", "Clearly doesn't fit anywhere else", "tray.fill", "gray"))
    return topics


# --- Reading --------------------------------------------------------------------------------------

def _path(p: str | os.PathLike) -> Path:
    return Path(str(p).strip()).expanduser()


def _from_raw(raw: dict, *, strict: bool = False) -> Config:
    def number(key: str, default: float, lo: float, hi: float) -> float:
        try:
            v = float(raw.get(key, default))
        except (TypeError, ValueError):
            if strict:
                raise ConfigError(f"“{key}” must be a number.") from None
            v = default
        return min(hi, max(lo, v))

    root = _path(raw.get("root") or "~/Downloads/Tidywise")
    watch = list(dict.fromkeys(_path(p) for p in raw.get("watch", ["~/Downloads"]) if str(p).strip()))
    subfolders = raw.get("subfolders", "none")
    if strict:
        if not root.is_absolute():
            raise ConfigError("The destination folder needs a full path.")
        if root in watch:
            raise ConfigError("The destination folder can't also be a folder Tidywise looks in. Pick a folder of its own, e.g. a subfolder.")
        if any(not p.is_absolute() for p in watch):
            raise ConfigError("Every folder Tidywise looks in needs a full path.")
        if not str(raw.get("model") or "").strip():
            raise ConfigError("No AI model is selected.")
        unknown = [b for b in raw.get("browsers") or [] if str(b).strip().lower() not in BROWSER_IDS]
        if unknown:
            raise ConfigError(f"Unknown browser “{unknown[0]}”. Tidywise can read: {', '.join(BROWSER_IDS)}.")
        if raw.get("links_enabled") and "browsers" in raw and not raw.get("browsers"):
            raise ConfigError("Pick at least one browser whose history Tidywise reads – or switch the link collection off.")
        if str(raw.get("llm_backend") or "ollama").strip() not in LLM_BACKENDS:
            raise ConfigError(f"“llm_backend” is one of: {', '.join(LLM_BACKENDS)}.")
        for key in ("ollama_url", "llm_url"):
            url = str(raw.get(key) or "").strip()
            if url and not url.startswith(("http://", "https://")):
                raise ConfigError(f"“{key}” needs to start with http:// or https://.")
            if url and not raw.get("allow_remote_llm") and not is_local_url(url):
                raise ConfigError(f"“{key}” points to another computer. Tidywise only talks to a model on this Mac "
                                  "(127.0.0.1 or localhost) – unless you set allow_remote_llm = true.")
    return Config(
        root=root,
        watch=watch,
        model=str(raw.get("model") or "gemma4:e4b-it-qat").strip(),
        ollama_url=str(raw.get("ollama_url") or "http://127.0.0.1:11434").strip().rstrip("/"),
        llm_backend=backend if (backend := str(raw.get("llm_backend") or "ollama").strip()) in LLM_BACKENDS else "ollama",
        llm_url=str(raw.get("llm_url") or "http://127.0.0.1:1234/v1").strip().rstrip("/"),
        allow_remote_llm=bool(raw.get("allow_remote_llm", False)),
        profile=str(raw.get("profile") or "").strip(),
        summary_language=str(raw.get("summary_language") or "English").strip(),
        min_age_minutes=number("min_age_minutes", 60, 0, 60 * 24 * 365),
        min_confidence=number("min_confidence", 0.6, 0, 1),
        auto_apply=bool(raw.get("auto_apply", False)),
        subfolders=subfolders if subfolders in SUBFOLDERS else "none",
        interval_minutes=int(number("interval_minutes", 15, 1, 24 * 60)),
        # If the entry is missing (older configuration) the history stays on – as it was before.
        links_enabled=bool(raw.get("links_enabled", True)),
        browsers=_browsers(raw.get("browsers")),
        chrome_profile=str(raw.get("chrome_profile") or "Default").strip(),
        links_first_days=int(number("links_first_days", 14, 1, 3650)),
        links_interval_minutes=int(number("links_interval_minutes", 60, 1, 24 * 60)),
        mcp_enabled=bool(raw.get("mcp_enabled", False)),
        skip_extensions=_extensions(raw.get("skip_extensions", [])),
        topics=_topics(raw.get("topics", []), strict=strict),
    )


def is_local_url(url: str) -> bool:
    """Does the address point to this Mac?"""
    import ipaddress
    from urllib.parse import urlsplit
    try:
        host = (urlsplit(url).hostname or "").lower()
    except ValueError:
        return False
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def _browsers(values) -> list[str]:
    """Known browsers in a fixed order; Chrome if nothing (valid) is set – that was the only one before."""
    chosen = {str(v).strip().lower() for v in values or []}
    return [b for b in BROWSER_IDS if b in chosen] or ["chrome"]


def _extensions(values) -> list[str]:
    out = []
    for e in values or []:
        e = str(e).strip().lower()
        if e:
            out.append(e if e.startswith(".") else "." + e)
    return list(dict.fromkeys(out))


def exists(path: Path | None = None) -> bool:
    return (path or CONFIG_PATH).exists()


def load(path: Path | None = None) -> Config:
    path = path or CONFIG_PATH
    if not path.exists():
        path = DEFAULT_CONFIG
    with open(path, "rb") as f:
        raw = tomllib.load(f)
    return _from_raw(raw)


# --- Writing --------------------------------------------------------------------------------------

def short_path(p: str | os.PathLike | None) -> str:
    """Write paths inside the home folder with ~ – easier to read and portable to another Mac."""
    if not p:
        return ""
    home = str(Path.home())
    s = str(p)
    return "~" + s[len(home):] if s == home or s.startswith(home + "/") else s


_short = short_path


def _q(s: str) -> str:
    return json.dumps(s, ensure_ascii=False)  # JSON strings are valid TOML strings


def _list(values: list[str]) -> str:
    return "[" + ", ".join(_q(v) for v in values) + "]"


def to_dict(cfg: Config, path: Path | None = None) -> dict:
    """The shape in which the app reads the settings and hands them back."""
    return {
        "exists": exists(path),
        "path": str(path or CONFIG_PATH),
        "root": str(cfg.root),
        "watch": [str(p) for p in cfg.watch],
        "model": cfg.model,
        "ollama_url": cfg.ollama_url,
        "llm_backend": cfg.llm_backend,
        "llm_url": cfg.llm_url,
        "allow_remote_llm": cfg.allow_remote_llm,
        "profile": cfg.profile,
        "summary_language": cfg.summary_language,
        "min_age_minutes": cfg.min_age_minutes,
        "min_confidence": cfg.min_confidence,
        "auto_apply": cfg.auto_apply,
        "subfolders": cfg.subfolders,
        "interval_minutes": cfg.interval_minutes,
        "links_enabled": cfg.links_enabled,
        "browsers": cfg.browsers,
        "chrome_profile": cfg.chrome_profile,
        "links_first_days": cfg.links_first_days,
        "links_interval_minutes": cfg.links_interval_minutes,
        "mcp_enabled": cfg.mcp_enabled,
        "skip_extensions": cfg.skip_extensions,
        "folder_names": {"topics": cfg.topics_dir.name, "days": cfg.days_dir.name, "private": cfg.private_dir.name,
                         "inbox": cfg.inbox_dir.name, "links": cfg.links_dir.name},
        "topics": [{"slug": t.slug, "name": t.name, "description": t.description, "symbol": t.symbol, "color": t.color}
                   for t in cfg.topics],
    }


def to_toml(cfg: Config) -> str:
    num = lambda x: str(int(x)) if float(x).is_integer() else str(x)  # noqa: E731
    lines = [
        "# Settings for Tidywise. The easiest way to change them is in the app under Settings.",
        "# Editing by hand works too – but the file is rewritten the next time the app saves.",
        "",
        "# Where files are sorted to. Topics/, Links/, Days/, _inbox/ and AGENTS.md are created here.",
        f"root = {_q(_short(cfg.root))}",
        "",
        "# Where Tidywise looks. Only loose files directly in these folders – subfolders are never touched.",
        f"watch = {_list([_short(p) for p in cfg.watch])}",
        "",
        "# Leave a file alone until it has been unchanged for this many minutes (running downloads, open work).",
        f"min_age_minutes = {num(cfg.min_age_minutes)}",
        "# false: suggestions only, you confirm. true: confident cases are moved automatically.",
        f"auto_apply = {str(cfg.auto_apply).lower()}",
        "# Below this confidence (0–1) a file goes to _inbox/ instead of a topic.",
        f"min_confidence = {num(cfg.min_confidence)}",
        '# Subfolders inside a topic: "none", "year" (Topics/finance/2026) or "month" (…/2026/2026-03).',
        f"subfolders = {_q(cfg.subfolders)}",
        "# How often the app checks while it is open (minutes).",
        f"interval_minutes = {cfg.interval_minutes}",
        "# Files with these extensions are never sorted.",
        f"skip_extensions = {_list(cfg.skip_extensions)}",
        "",
        "# The local AI model (name as in `ollama pull`) and the address of Ollama.",
        f"model = {_q(cfg.model)}",
        f"ollama_url = {_q(cfg.ollama_url)}",
        '# "ollama", or "openai" for another local server with an OpenAI-style API – LM Studio, mlx-lm, llama.cpp.',
        "# Then `model` is the name that server uses, and llm_url its address including /v1.",
        f"llm_backend = {_q(cfg.llm_backend)}",
        f"llm_url = {_q(cfg.llm_url)}",
        "# Only true lets Tidywise send file contents to a model server on another computer.",
        f"allow_remote_llm = {str(cfg.allow_remote_llm).lower()}",
        "# A sentence about you helps the model sort, e.g. \"Architecture student, working part-time at a café\".",
        f"profile = {_q(cfg.profile)}",
        "# The language summaries and keywords are written in – best the one you search in.",
        f"summary_language = {_q(cfg.summary_language)}",
        "",
        "# Read the browser history as a link collection (stays local). Browsers: " + ", ".join(BROWSER_IDS) + ".",
        "# Safari needs Full Disk Access. chrome_profile = folder under ~/Library/Application Support/Google/Chrome/,",
        "# then: days to go back the first time, read at most this often (minutes).",
        f"links_enabled = {str(cfg.links_enabled).lower()}",
        f"browsers = {_list(cfg.browsers)}",
        f"chrome_profile = {_q(cfg.chrome_profile)}",
        f"links_first_days = {cfg.links_first_days}",
        f"links_interval_minutes = {cfg.links_interval_minutes}",
        "",
        "# Let AI assistants (Claude Code, Codex, OpenCode, …) search the archive through `tidywise mcp`.",
        "# Read-only, and only while the Tidywise app is open.",
        f"mcp_enabled = {str(cfg.mcp_enabled).lower()}",
        "",
        "# Topics. slug = folder name. The model reads the description – the more specific, the better.",
    ]
    for t in cfg.topics:
        lines += ["[[topics]]", f"slug = {_q(t.slug)}", f"name = {_q(t.name)}", f"description = {_q(t.description)}",
                  f"symbol = {_q(t.symbol)}", f"color = {_q(t.color)}", ""]
    return "\n".join(lines)


def save(raw: dict, path: Path | None = None) -> Config:
    """Validate the settings and write them. Raises ConfigError with a sentence saying what is wrong."""
    path = path or CONFIG_PATH
    cfg = _from_raw(raw, strict=True)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(to_toml(cfg), encoding="utf-8")
    os.replace(tmp, path)
    return cfg


def update(changes: dict, path: Path | None = None) -> Config:
    """Change single values; everything else stays."""
    raw = to_dict(load(path), path)
    raw.update(changes)
    return save(raw, path)
