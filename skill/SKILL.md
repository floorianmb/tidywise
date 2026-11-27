---
name: tidywise
description: The user's local knowledge archive (default ~/Downloads/Tidywise) – sorted downloads/documents, link collection and searches from the browser history, daily logs. Use ONLY when the user explicitly asks you to look on their computer, in their files, downloads or browser history (e.g. "look on my Mac", "get context from my computer", "what did I save/read about X", "/tidywise" – in any language, e.g. German "guck mal auf meinem Rechner"). Never load it on your own initiative or as a precaution.
---

# Tidywise – getting context from the user's Mac

The archive is maintained by a local background service (`tidywise`, a local model through Ollama). You only read it when the user has explicitly asked you to.

**Where is it?** The default is `~/Downloads/Tidywise`. The user can change the location – `tidywise config` shows it in the `root` line and the folders it looks in under `watch`. Below, `~/Downloads/Tidywise` stands for that location.

**Folder names.** New installs use `Topics/`, `Days/` and `_private/`. Installs set up before the project switched to English keep `Themen/`, `Tage/` and `_privat/`. `AGENTS.md` in the archive always names the folders that actually exist – go by that.

## Rules
- Never open, list or search the private folder (`_private/` or `_privat/`). Always exclude it in `grep`/`find`.
- Read only as much as the current question needs. Don't load the whole archive.
- Don't move, delete or rename anything in the archive – unless the user explicitly wants it (then through `tidywise set` / `tidywise undo`).
- The Markdown files are generated; manual changes are overwritten.

## How to proceed
1. Read `~/Downloads/Tidywise/AGENTS.md`: topic overview, the last 14 days, folders that stayed in place.
2. Narrow down to fit the question:
   - Topic known → `~/Downloads/Tidywise/Topics/<topic>/_index.md` (file, summary, keywords, source).
   - Time span known ("yesterday", "last week") → `~/Downloads/Tidywise/Days/YYYY-MM-DD.md`.
   - Term known → `grep -ril "<term>" ~/Downloads/Tidywise --include="*.md" --exclude-dir=_private --exclude-dir=_privat`
   - Web pages read → `~/Downloads/Tidywise/Links/<topic>.md`.
3. Only then open the specific original files that look relevant (PDF, code, document). For projects that sit as folders in the watched folders (default: `~/Downloads`, `~/Documents`, `~/Desktop`), look there directly.
4. Briefly tell the user which sources you found and read before you build on them.

## Useful commands
- `tidywise status` – state, open suggestions
- `tidywise scan` – classify new loose files (suggestions only)
- `tidywise links` – read the browser history now (only if the user has turned the link collection on)
- `tidywise config` – settings: destination folder, watched folders, topics
