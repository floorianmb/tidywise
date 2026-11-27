"""tidywise – sort loose files on a Mac with a local AI model and make them findable for agents."""
from __future__ import annotations

import argparse
import fcntl
import json
import signal
import sys
from datetime import datetime
from pathlib import Path

from . import assistants, chrome, config, llm, mcp, organize, render, service, telemetry
from .config import CONFIG_PATH, load, short_path
from .store import Store


def _short(p: str) -> str:
    return short_path(p)


def print_plan(cfg, rows) -> None:
    if not rows:
        print("No suggestions – nothing to sort.")
        return
    print(f"{'No.':>4}  {'Topic':<24} {'Conf.':>5}  File  →  What it's about")
    for r in rows:
        dest = organize.destination(cfg, r)
        target = "_inbox" if dest.parent == cfg.inbox_dir else r["topic"]
        if r["new_topic"] and target == "_inbox":
            target = f"_inbox (new: {r['new_topic']})"
        print(f"{r['id']:>4}  {target[:24]:<24} {(r['confidence'] or 0):>5.2f}  {_short(r['orig_path'])}")
        print(f"{'':>6}{r['summary']}")


def _check_llm(cfg) -> None:
    ok, msg = llm.available(cfg)
    if not ok:
        sys.exit(msg)


def cmd_scan(cfg, store, args):
    _check_llm(cfg)
    rows = organize.plan(cfg, store, verbose=not args.quiet)
    render.render(cfg, store)
    print_plan(cfg, rows)
    if rows:
        print("\nMove with: tidywise apply   (some: tidywise apply 3 7, change topic: tidywise set <no.> <topic>)")


def cmd_apply(cfg, store, args):
    telemetry.set_activity(cfg, "Moving files")
    done = organize.apply(cfg, store, ids=args.ids or None)
    for src, dst in done:
        print(f"  {_short(src)}  →  {_short(dst)}")
    render.render(cfg, store)
    # The app cuts the summary at "Undo: tidywise".
    print(f"{len(done)} file(s) moved. Undo: tidywise undo --last {len(done)}" if done else "Nothing moved.")


def cmd_set(cfg, store, args):
    organize.set_topic(cfg, store, args.id, args.topic)
    render.render(cfg, store)
    print(f"No. {args.id} → {args.topic}")


def cmd_undo(cfg, store, args):
    if not (args.last or args.ids or args.all):
        sys.exit("Please give --last N, --all or numbers.")
    done = organize.undo(cfg, store, last=None if args.all else args.last, ids=args.ids or None)
    for src, dst in done:
        print(f"  {_short(src)}  →  {_short(dst)}")
    render.render(cfg, store)
    print(f"{len(done)} file(s) put back.")


def cmd_links(cfg, store, args):
    if not cfg.links_enabled:
        sys.exit("The Chrome history is turned off. Turn it on: tidywise config set links_enabled true")
    _check_llm(cfg)
    new, searches = chrome.sync(cfg, store, verbose=not args.quiet, force=True)
    render.render(cfg, store)
    print(f"{new} new links, {searches} searches read.")


def cmd_relink(cfg, store, args):
    deleted, merged = chrome.prune_noise(store)
    print(f"Cleaned: {deleted} noise links deleted, {merged} merged.")
    if args.prune_only:
        render.render(cfg, store)
        return
    _check_llm(cfg)
    topics = None if args.all else (args.topics or [cfg.fallback])
    n = chrome.reclassify(cfg, store, topics, verbose=not args.quiet)
    render.render(cfg, store)
    counts = store.db.execute("SELECT topic, COUNT(*) FROM links GROUP BY topic ORDER BY 2 DESC").fetchall()
    print(f"{n} links re-sorted. Distribution:")
    for topic, c in counts:
        print(f"  {c:>5}  {topic}")


def cmd_render(cfg, store, args):
    render.render(cfg, store)
    print(f"Index updated: {_short(str(cfg.root / 'AGENTS.md'))}")


def cmd_status(cfg, store, args):
    ok, msg = llm.available(cfg)
    db = store.db
    count = lambda q: db.execute(q).fetchone()[0]  # noqa: E731
    print(f"Settings:      {_short(str(CONFIG_PATH)) if CONFIG_PATH.exists() else '(defaults)'}")
    print(f"Destination:   {_short(str(cfg.root))}")
    print(f"Model:         {cfg.model} – {msg}")
    print(f"Looks in:      {', '.join(_short(str(p)) for p in cfg.watch)}")
    print(f"Automatic:     {'moves by itself' if cfg.auto_apply else 'suggestions only'}")
    print(f"Service:       {'set up' if service.loaded() else 'not set up (tidywise service install)'}")
    planned = count("SELECT COUNT(*) FROM files WHERE status='planned'")
    moved = count("SELECT COUNT(*) FROM files WHERE status='moved'")
    print(f"Suggestions:   {planned}")
    print(f"Sorted:        {moved}")
    print(f"Links:         {count('SELECT COUNT(*) FROM links')}")
    print(f"Last run:      {store.get_meta('agent_last_run', '–')}")


# --- Settings and service: need neither database nor model ------------------------------------------

def _parse_value(key: str, value: str):
    """Convert text from the command line to the right type – going by the type of the current value."""
    current_data = config.to_dict(load())
    if key not in current_data or key in ("exists", "path", "topics", "folder_names"):
        sys.exit(f"Unknown setting “{key}”. Show all: tidywise config")
    current = current_data.get(key)
    if isinstance(current, bool):
        if value.lower() not in ("true", "false", "on", "off", "1", "0"):
            sys.exit(f"“{key}” is on or off: true / false")
        return value.lower() in ("true", "on", "1")
    if isinstance(current, (int, float)):
        try:
            return float(value.replace(",", "."))
        except ValueError:
            sys.exit(f"“{key}” must be a number.")
    if isinstance(current, list):
        return [v.strip() for v in value.split(",") if v.strip()]
    return value


def cmd_config(args) -> None:
    try:
        if args.action == "save":  # this is how the app saves: the whole configuration as JSON
            config.save(json.loads(args.json if args.json is not None else sys.stdin.read()))
        elif args.action == "init":
            if not config.exists():
                config.save(config.to_dict(load()))
        elif args.action == "set":
            if len(args.rest) < 2:
                sys.exit("Usage: tidywise config set <name> <value>   (lists with commas: watch ~/Downloads,~/Desktop)")
            key = args.rest[0]
            config.update({key: _parse_value(key, " ".join(args.rest[1:]))})
        elif args.action == "path":
            print(CONFIG_PATH)
            return
    except config.ConfigError as e:
        sys.exit(str(e))
    except json.JSONDecodeError as e:
        sys.exit(f"Not valid JSON: {e}")
    data = config.to_dict(load())
    if args.as_json:
        print(json.dumps(data, ensure_ascii=False))
        return
    if args.action in ("save", "set", "init"):
        print(f"Saved: {_short(str(CONFIG_PATH))}")
        return
    print(f"# {_short(data['path'])}{'' if data['exists'] else '  (not created yet – defaults)'}")
    for key, value in data.items():
        if key in ("exists", "path", "topics", "folder_names"):
            continue
        if isinstance(value, list):
            value = ", ".join(_short(str(v)) for v in value)
        print(f"{key:<24} {_short(str(value)) if isinstance(value, str) else value}")
    print(f"{'topics':<24} {', '.join(t['slug'] for t in data['topics'])}")


def cmd_service(args) -> None:
    if args.action == "install":
        try:
            service.install()
        except RuntimeError as e:
            sys.exit(str(e))
        print("Service set up. It only runs while Tidywise.app is open.")
        print(f"To sort in the background this program needs Full Disk Access:\n  {service.python_binary()}")
    elif args.action == "uninstall":
        service.uninstall()
        print("Service removed.")
    else:
        print(json.dumps({"loaded": service.loaded(), "python": service.python_binary(), "log": str(service.LOG)},
                         ensure_ascii=False))


def cmd_mcp(args) -> None:
    if args.action == "serve":  # started by the assistant; speaks MCP on stdin/stdout until it disconnects
        return mcp.serve(always=args.always)
    try:
        if args.action in ("add", "remove"):
            if not args.client:
                sys.exit(f"Which assistant? tidywise mcp {args.action} <{'|'.join(c.id for c in assistants.CLIENTS)}>")
            if args.action == "add":
                message = assistants.connect(args.client)
                if not config.exists():  # writing a config now would make the app skip its setup
                    message += ("\nTidywise isn't set up yet – searching is switched on when you finish setup in the app"
                                " (without the app: tidywise config set mcp_enabled true).")
                elif not load().mcp_enabled:
                    config.update({"mcp_enabled": True})  # connecting an assistant means: yes, let it search
            else:
                message = assistants.disconnect(args.client)
            if not args.as_json:
                print(message)
    except assistants.AssistantError as e:
        sys.exit(str(e))
    data = assistants.status()
    if args.as_json:
        print(json.dumps(data, ensure_ascii=False))
    elif args.action == "status":
        cfg = load()
        print(f"Assistants may search:  {'yes' if cfg.mcp_enabled else 'no (tidywise config set mcp_enabled true)'}"
              f"{'' if mcp.app_is_open(cfg) else '   – the Tidywise app is closed, so the server will not answer'}")
        for c in data["clients"]:
            state = "connected" if c["connected"] else ("not connected" if c["installed"] else "not installed")
            print(f"  {c['title']:<15} {state}")
        print(f"\nConnect one:  tidywise mcp add <{'|'.join(c['id'] for c in data['clients'])}>")
        print(f"Anything else: add this to its MCP settings\n{data['snippet']}")


def run_lock(cfg, wait: bool):
    """Shared lock for everything that uses the model – so two runs never write at the same time."""
    lock = open(cfg.state_dir / "agent.lock", "w")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return lock
    except BlockingIOError:
        if not wait:
            lock.close()
            return None
    print("Waiting for the current run to finish …", flush=True)
    fcntl.flock(lock, fcntl.LOCK_EX)
    return lock


def _trim_log(max_bytes: int = 2_000_000, keep_bytes: int = 200_000) -> None:
    """Keep agent.log bounded so long-running installs don't grow the file forever."""
    try:
        if service.LOG.exists() and service.LOG.stat().st_size > max_bytes:
            data = service.LOG.read_bytes()[-keep_bytes:]
            nl = data.find(b"\n")
            service.LOG.write_bytes(data[nl + 1:] if nl != -1 else data)
    except OSError:
        pass


def cmd_agent(cfg, store, args):
    """One run, triggered by the app through launchd (files + history if enabled + index). Never runs twice at once."""
    lock = run_lock(cfg, wait=False)  # something already running (service or CLI)? Then skip this time
    if lock is None:
        return
    _trim_log()
    print(f"--- {datetime.now():%Y-%m-%d %H:%M:%S}")
    ok, msg = llm.available(cfg)
    if not ok:
        print(msg)
        return
    rows = organize.plan(cfg, store, verbose=False)
    if cfg.auto_apply and rows:
        for src, dst in organize.apply(cfg, store):
            print(f"  {_short(src)} → {_short(dst)}")
    elif rows:
        print(f"  {len(rows)} suggestions open (auto_apply = false)")
    if cfg.links_enabled:
        try:
            new, _ = chrome.sync(cfg, store, verbose=False)
            if new:
                print(f"  {new} new links")
        except (FileNotFoundError, PermissionError) as e:
            print(f"  Chrome history: {e}")
    render.render(cfg, store)
    store.set_meta("agent_last_run", datetime.now().isoformat(timespec="seconds"))
    store.commit()


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(prog="tidywise", description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("scan", help="classify loose files, suggestions only (moves nothing)")
    p.add_argument("-q", "--quiet", action="store_true")
    p = sub.add_parser("apply", help="carry out suggestions (all, or only the given numbers)")
    p.add_argument("ids", nargs="*", type=int)
    p = sub.add_parser("set", help="set the topic for a file (also after it was moved)")
    p.add_argument("id", type=int)
    p.add_argument("topic")
    p = sub.add_parser("undo", help="undo moves")
    p.add_argument("ids", nargs="*", type=int)
    p.add_argument("--last", type=int)
    p.add_argument("--all", action="store_true")
    p = sub.add_parser("links", help="read the Chrome history now")
    p.add_argument("-q", "--quiet", action="store_true")
    p = sub.add_parser("relink", help="clean links and re-sort them (default: only the catch-all topic)")
    p.add_argument("topics", nargs="*", help="topics whose links are re-sorted")
    p.add_argument("--all", action="store_true", help="re-sort all links")
    p.add_argument("--prune-only", action="store_true", help="only remove noise, re-sort nothing")
    p.add_argument("-q", "--quiet", action="store_true")
    sub.add_parser("render", help="rewrite AGENTS.md and the index files")
    sub.add_parser("status", help="show the current state")
    sub.add_parser("agent", help="one background run (the app triggers this regularly)")
    p = sub.add_parser("config", help="show or change settings (tidywise config set auto_apply true)")
    p.add_argument("action", nargs="?", default="show", choices=["show", "set", "save", "init", "path"])
    p.add_argument("rest", nargs="*", help="with set: <name> <value>")
    p.add_argument("--json", dest="json", help="with save: the whole configuration as JSON (otherwise read from stdin)")
    p.add_argument("--as-json", action="store_true", help="print the result as JSON (for the app)")
    p = sub.add_parser("service", help="set up or remove the background service")
    p.add_argument("action", choices=["install", "uninstall", "status"])
    p = sub.add_parser("mcp", help="MCP server for AI assistants, and connecting them (tidywise mcp add claude)")
    p.add_argument("action", nargs="?", default="serve", choices=["serve", "add", "remove", "status"])
    p.add_argument("client", nargs="?", help="with add/remove: " + ", ".join(c.id for c in assistants.CLIENTS))
    p.add_argument("--always", action="store_true",
                   help="answer even when the Tidywise app is closed or assistants are switched off (setups without the app)")
    p.add_argument("--as-json", action="store_true", help="print the result as JSON (for the app)")
    args = ap.parse_args(argv)

    if args.cmd == "mcp":
        return cmd_mcp(args)
    if args.cmd == "config":
        return cmd_config(args)
    if args.cmd == "service":
        return cmd_service(args)

    # The app ends runs with SIGTERM (AI off / app quit). Raise it as SystemExit
    # so `finally` resets the status and SQLite rolls back cleanly.
    signal.signal(signal.SIGTERM, _on_sigterm)

    cfg = load()
    cfg.ensure_dirs()
    store = Store(cfg.db_path)
    lock = run_lock(cfg, wait=True) if args.cmd in ("scan", "apply", "links", "relink", "set", "undo") else None
    try:
        _dispatch(args, cfg, store)
    finally:
        telemetry.clear_activity(cfg)


def _on_sigterm(signum, frame) -> None:
    print("  cancelled", flush=True)
    raise SystemExit(128 + signum)


def _dispatch(args, cfg, store) -> None:
    {
        "scan": cmd_scan, "apply": cmd_apply, "set": cmd_set, "undo": cmd_undo, "links": cmd_links,
        "render": cmd_render, "status": cmd_status, "agent": cmd_agent, "relink": cmd_relink,
    }[args.cmd](cfg, store, args)


if __name__ == "__main__":
    main()
