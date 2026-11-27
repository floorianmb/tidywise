"""Generate Markdown files from the database: AGENTS.md, Topics/*/_index.md, Links/*.md, Days/*.md."""
from __future__ import annotations

import os
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path
from urllib.parse import quote

from .config import Config, Topic, short_path
from .store import Store

RECENT_DAYS = 14
LINKS_PER_TOPIC_PER_DAY = 8


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists() or path.read_text(encoding="utf-8") != text:
        path.write_text(text, encoding="utf-8")


def _remove_stale(path: Path, header: str) -> None:
    """Delete a file this module wrote earlier (recognised by its first line) – never a note of the user's."""
    try:
        with open(path, encoding="utf-8") as f:
            first = f.readline()
    except (OSError, UnicodeDecodeError):
        return
    if first.startswith(header):
        path.unlink(missing_ok=True)


def _rel(cfg: Config, p: str | Path) -> str:
    try:
        return quote(str(Path(p).relative_to(cfg.root)))
    except ValueError:
        return quote(str(p))


def _md(s: str | None) -> str:
    return (s or "").replace("|", "/").replace("\n", " ").strip()


def _md_label(s: str | None) -> str:
    return _md(s).replace("[", "\\[").replace("]", "\\]")


def _md_url(url: str) -> str:
    return url.replace(" ", "%20").replace("(", "%28").replace(")", "%29")


def _day(iso: str | None) -> str:
    return (iso or "")[:10]


def render(cfg: Config, store: Store) -> None:
    cfg.ensure_dirs()
    db = store.db
    moved = db.execute("SELECT * FROM files WHERE status='moved' ORDER BY moved_at DESC").fetchall()
    links = db.execute("SELECT * FROM links WHERE topic IS NOT NULL ORDER BY last_visit DESC").fetchall()
    searches = db.execute("SELECT * FROM searches ORDER BY at DESC").fetchall()
    names = {t.slug: t.name for t in cfg.topics}
    links_name = cfg.links_dir.name  # folder names as they are on disk (older installs keep Themen/, Tage/, _privat/)

    files_by_topic = defaultdict(list)
    for r in moved:
        key = "_inbox" if Path(r["cur_path"]).parent.name == "_inbox" else r["topic"]
        files_by_topic[key].append(r)
    links_by_topic = defaultdict(list)
    for r in links:
        links_by_topic[r["topic"]].append(r)

    # Topics/<slug>/_index.md (keep rendering removed topics that still hold sorted files)
    known_slugs = {t.slug for t in cfg.topics}
    extra_slugs = sorted((set(files_by_topic) | set(links_by_topic)) - known_slugs - {"_inbox", None, ""})
    all_topics = list(cfg.topics) + [Topic(slug=s, name=s) for s in extra_slugs]
    active_topic_slugs = {t.slug for t in all_topics}

    for t in all_topics:
        rows = files_by_topic.get(t.slug, [])
        if t.slug not in known_slugs and not rows:
            continue
        lines = [f"# {t.name}", ""] + ([f"_{t.description}_", ""] if t.description else []) + [
            f"Links on this topic: [{links_name}/{t.slug}.md](../../{links_name}/{t.slug}.md) ({len(links_by_topic.get(t.slug, []))})", ""]
        if rows:
            lines += ["| Date | File | What it's about | Keywords | Source |", "|---|---|---|---|---|"]
            for r in rows:
                src = f"[Link]({_md_url(r['source_url'])})" if r["source_url"] else ""
                link = quote(os.path.relpath(r["cur_path"], cfg.topics_dir / t.slug))  # also from year subfolders
                lines.append(f"| {_day(r['moved_at'])} | [{_md_label(r['name'])}]({link}) | "
                             f"{_md(r['summary'])} | {_md(r['keywords'])} | {src} |")
        else:
            lines.append("_No files yet._")
        _write(cfg.topics_dir / t.slug / "_index.md", "\n".join(lines) + "\n")

    for p in cfg.topics_dir.glob("*/_index.md"):
        if p.parent.name not in active_topic_slugs or (p.parent.name not in known_slugs and not files_by_topic.get(p.parent.name)):
            _remove_stale(p, "# ")

    # _inbox/_index.md
    rows = files_by_topic.get("_inbox", [])
    lines = ["# Inbox – unsure classification", "",
             "Sort properly with `tidywise set <no.> <topic>`.", "",
             "| No. | File | Suggestion | Confidence | What it's about |", "|---|---|---|---|---|"]
    for r in rows:
        suggestion = r["new_topic"] and f"new: {r['new_topic']}" or r["topic"]
        actual_name = Path(r["cur_path"]).name if r["cur_path"] else r["name"]
        lines.append(f"| {r['id']} | [{_md_label(r['name'])}]({quote(actual_name)}) | {_md(suggestion)} | "
                     f"{(r['confidence'] or 0):.1f} | {_md(r['summary'])} |")
    _write(cfg.inbox_dir / "_index.md", "\n".join(lines) + "\n")

    # Links/<slug>.md
    for slug, rows in links_by_topic.items():
        lines = [f"# Links: {names.get(slug, slug)}", ""]
        month = None
        for r in rows:
            m = _day(r["last_visit"])[:7]
            if m != month:
                lines += ["", f"## {m}"]
                month = m
            visits = f" ({r['visits']}×)" if (r["visits"] or 0) > 1 else ""
            lines.append(f"- {_day(r['last_visit'])} [{_md_label(r['title'])}]({_md_url(r['url'])}) – {r['domain']}{visits}")
        _write(cfg.links_dir / f"{slug}.md", "\n".join(lines) + "\n")
    for p in cfg.links_dir.glob("*.md"):
        if p.stem not in links_by_topic:
            _remove_stale(p, "# Links: ")

    # Days/YYYY-MM-DD.md
    by_day_files = defaultdict(list)
    for r in moved:
        by_day_files[_day(r["moved_at"])].append(r)
    by_day_links = defaultdict(list)
    for r in links:
        by_day_links[_day(r["last_visit"])].append(r)
    by_day_search = defaultdict(list)
    for r in searches:
        by_day_search[_day(r["at"])].append(r["term"])
    active_days = set(by_day_files) | set(by_day_links) | set(by_day_search)
    for day in active_days:
        lines = [f"# {day}", ""]
        if by_day_files.get(day):
            lines.append("## Files sorted")
            for r in by_day_files[day]:
                lines.append(f"- [{_md_label(r['name'])}](../{_rel(cfg, r['cur_path'])}) – {_md(r['summary'])}")
            lines.append("")
        if by_day_search.get(day):
            lines.append("## Searched for")
            lines += [f"- {_md(t)}" for t in dict.fromkeys(by_day_search[day])]
            lines.append("")
        if by_day_links.get(day):
            lines.append("## Read (by topic)")
            grouped = defaultdict(list)
            for r in by_day_links[day]:
                grouped[r["topic"]].append(r)
            for slug, rs in sorted(grouped.items(), key=lambda kv: -len(kv[1])):
                lines.append(f"### {names.get(slug, slug)} ({len(rs)})")
                for r in rs[:LINKS_PER_TOPIC_PER_DAY]:
                    lines.append(f"- [{_md_label(r['title'])}]({_md_url(r['url'])})")
                if len(rs) > LINKS_PER_TOPIC_PER_DAY:
                    lines.append(f"- … {len(rs) - LINKS_PER_TOPIC_PER_DAY} more in [{links_name}/{slug}.md](../{links_name}/{slug}.md)")
            lines.append("")
        _write(cfg.days_dir / f"{day}.md", "\n".join(lines) + "\n")
    for p in cfg.days_dir.glob("*.md"):
        if p.stem not in active_days and len(p.stem) == 10 and p.stem[4] == p.stem[7] == "-":
            _remove_stale(p, f"# {p.stem}\n")

    _write(cfg.root / "AGENTS.md", _agents_md(cfg, all_topics, moved, links, searches, files_by_topic, links_by_topic))


def _agents_md(cfg, all_topics, moved, links, searches, files_by_topic, links_by_topic) -> str:
    topics_name, days_name, links_name = cfg.topics_dir.name, cfg.days_dir.name, cfg.links_dir.name
    inbox_name, private_name = cfg.inbox_dir.name, cfg.private_dir.name
    lines = [
        "# Tidywise – entry point for agents",
        "",
        f"> Generated automatically by `tidywise` on {datetime.now():%Y-%m-%d %H:%M}. Will be overwritten – do not edit by hand.",
        f"> Read only when the user explicitly asks. Never open or search the `{private_name}/` folder.",
        "",
        "## Finding your way around",
        f"- `{topics_name}/<topic>/_index.md` – per file: date, summary, keywords, download source. The files sit next to it.",
        f"- `{links_name}/<topic>.md` – link collection from the browser history, by month (only if turned on).",
        f"- `{days_name}/YYYY-MM-DD.md` – what was sorted, searched for and read on that day.",
        f"- `{inbox_name}/_index.md` – files with an unsure classification.",
        f'- Search: `grep -ril "<term>" {short_path(cfg.root)} --include="*.md" --exclude-dir={private_name}`,'
        " then open the hits and the original files selectively.",
        "- Folders (projects, unpacked downloads) are never moved – they stay where they are (see below).",
        "",
        "## Topics",
        "| Topic | Folder | Files | Links | Last active |",
        "|---|---|---|---|---|",
    ]
    known_slugs = {t.slug for t in cfg.topics}
    for t in all_topics:
        fs, ls = files_by_topic.get(t.slug, []), links_by_topic.get(t.slug, [])
        last = max([_day(r["moved_at"]) for r in fs] + [_day(r["last_visit"]) for r in ls] or [""])
        folder_cell = (f"[{topics_name}/{t.slug}]({topics_name}/{t.slug}/_index.md)"
                       if t.slug in known_slugs or fs else f"[{links_name}/{t.slug}.md]({links_name}/{t.slug}.md)")
        lines.append(f"| {_md(t.name)} | {folder_cell} | {len(fs)} | {len(ls)} | {last} |")
    inbox = files_by_topic.get("_inbox", [])
    if inbox:
        lines.append(f"| Inbox (unsure) | [{inbox_name}]({inbox_name}/_index.md) | {len(inbox)} | – | |")

    lines += ["", f"## Last {RECENT_DAYS} days", ""]
    since = (date.today() - timedelta(days=RECENT_DAYS)).isoformat()
    days = sorted({_day(r["moved_at"]) for r in moved} | {_day(r["last_visit"]) for r in links}
                  | {_day(r["at"]) for r in searches}, reverse=True)
    names = {t.slug: t.name for t in all_topics}
    for day in [d for d in days if d >= since]:
        fs = [r for r in moved if _day(r["moved_at"]) == day]
        topic_counts = Counter(r["topic"] for r in links if _day(r["last_visit"]) == day)
        n_search = sum(1 for r in searches if _day(r["at"]) == day)
        parts = []
        if fs:
            parts.append(f"{len(fs)} file(s): " + ", ".join(_md(r["name"]) for r in fs[:5]) + (" …" if len(fs) > 5 else ""))
        if topic_counts:
            parts.append("read: " + ", ".join(f"{names.get(s, s)} ({c})" for s, c in topic_counts.most_common(4)))
        if n_search:
            parts.append(f"{n_search} searches")
        lines.append(f"- [{day}]({days_name}/{day}.md): " + "; ".join(parts))

    lines += ["", "## Folders that stay where they are", ""]
    for folder in cfg.watch:
        try:
            subs = sorted(p.name for p in folder.iterdir()
                          if p.is_dir() and not p.name.startswith(".") and p != cfg.root)
        except OSError:
            continue
        if subs:
            lines.append(f"- `{short_path(folder)}/`: " + ", ".join(f"`{s}`" for s in subs))
    return "\n".join(lines) + "\n"
