# VFX Asset Browser

A **local-first** asset browser for VFX pipelines.
It indexes the media folders you already have, generates near-instant thumbnails 
and short video proxies, and gives you search, filters, metadata, favorites, and Nuke-ready
file paths — **without ever touching your originals.**

Built with Python (FastAPI + SQLite), FFmpeg, and a no-build vanilla JS dark UI.
Everything runs on your machine; no cloud, no accounts, no network calls.

## Features

- **Instant previews** — 480px thumbnails and ≤4s video proxies; hover a card and
  the proxy plays in place. No opening the original, no waiting.
- **Image sequences as one asset** — `explosion.%04d.exr` is a single card with
  frame range, padding, and missing-frame detection — not 2,000 cards.
- **Search & filter** — by name/tags/category, kind (video/image/sequence),
  format, alpha (has / no / **unknown** — a failed probe is never reported as
  "no alpha"), minimum width, and sort order.
- **Alpha-aware** — native PNG and OpenEXR header readers (no EXR library
  dependency) report dimensions, bit depth, channels, and alpha.
- **Library management** — multiple roots, auto-detected categories, favorites,
  free-text tags, notes.
- **Workflow actions** — copy absolute path (sequences copy the canonical
  `dir/base.%04d.ext` pattern, Nuke-ready), reveal in Explorer with the file
  selected, open in viewer, reprocess a single asset.
- **Background pipeline** — 2 workers run metadata → thumbnail → proxy; new files
  in watched folders are picked up automatically (watchdog) and rescan is
  incremental.
- **Resilient** — missing files are detected and hidden (listed under
  *Missing files*), corrupt media fails with the exact error, interrupted jobs
  recover on restart, and restarting never regenerates existing previews.
  
<img src="https://github.com/acidneuron/vfx-browser/blob/main/vfxbrowser.png" alt="Descriptive Alt Text">

## Requirements

| Requirement | Notes |
|---|---|
| Windows 10/11 | Tested on Windows 11. Fresh installs have **none** of the tools below — see Quick Start step 1 |
| Python 3.12+ | A venv is created for you |
| FFmpeg + ffprobe on PATH | Used for metadata and previews (`ffmpeg -version` to verify) |
| `uv` | Only for initial setup (creates the venv) |
| git | Only to clone the repo (a zip download works too) |

## Quick Start

### 1. One-time prerequisites (fresh Windows 11)

Windows 11 ships with [winget](https://aka.ms/getwinget) preinstalled. In a
terminal (PowerShell or cmd):

```powershell
winget install Python.Python.3.12
winget install Gyan.FFmpeg
winget install astral-sh.uv
winget install Git.Git
```

**Close and reopen the terminal** so PATH picks the new tools up, then verify:
`python --version` · `ffmpeg -version` · `uv --version` · `git --version`.

Already have them? Skip straight to step 2 — the only runtime needs are
Python 3.12+ and FFmpeg on PATH.

### 2. Get the app and run it

1.git clone <repo-url> vfx-browser
cd vfx-browser

2.Run install_requirements.bat   :: checks uv + ffmpeg, creates .venv, installs everything

3.Run run.bat
```

Open **http://127.0.0.1:8765** in your browser, add a media folder under
**Add root** in the sidebar, and cards start appearing as previews are generated.

## How It Works

- **Backend** — FastAPI on `http://127.0.0.1:8765`, SQLite catalog in `data/vfx.db`.
- **Derivatives** — FFmpeg produces thumbnails, previews (alpha composited over a
  checkerboard), and short proxies; all derived files live under `cache/` and are
  safe to delete.
- **Originals are never modified, moved, or copied.** The browser references your
  files in place.
- **Sequences** are stored as one pattern row (`dir/base.%04d.ext`); all stored
  paths are canonical (forward slashes).
- **Frontend** — vanilla HTML/CSS/JS (no build step), dark VFX theme.

## Project Layout

```
vfx-browser/

├── main.py         # FastAPI app, API routes, watchdog, asset actions

├── db.py           # SQLite schema + canonical path/sequence helpers

├── media.py        # ffprobe wrapper + native PNG/EXR readers

├── proxy.py        # Thumbnail / preview / proxy generation

├── scanner.py      # Incremental scan, sequence grouping, missing detection

├── jobs.py         # Background job queue (2 workers)

├── static/         # Vanilla HTML/CSS/JS UI

├── data/           # SQLite catalog (the only app state)

├── cache/          # Derived files (thumbs/, proxies/, previews/)

├── requirements.txt

├── install_requirements.bat

├── run.bat

├── USAGE.md        # Full user guide

└── CREDITS.md      # Dependencies, licenses, references

```

## API (summary)

Base URL: `http://127.0.0.1:8765`

| Endpoint | Purpose |
|---|---|
| `GET /api/status` | Library / job / scan status |
| `POST /api/roots` · `GET /api/roots` | Add / list library roots |
| `POST /api/roots/{id}/rescan` | Rescan a root |
| `GET /api/assets?q_=&kind=&format=&alpha=&category=&root=&fav=&missing_only=&sort=&page=&limit=` | List & filter assets |
| `GET /api/asset/{id}` · `POST /api/asset/{id}` | Read / update (tags, notes, favorite, fps) |
| `POST /api/asset/{id}/action` | `copy_path`, `nuke_path`, `reveal`, `open`, `fav`, `reprocess` |
| `GET /api/asset/{id}/thumb` · `/proxy` · `/preview` | Derived files |
| `GET /api/facets` | Formats & categories present |
| `POST /api/jobs/retry-failed` | Requeue failed jobs |

Full reference: [USAGE.md](USAGE.md) §10.

## Status & Roadmap

**Stage 01 (this release)** — core browser: indexing, previews, search/filter,
favorites, missing-file detection, job recovery, restart persistence. Verified
end-to-end.

**Stage 02 (planned)** — FTS5 full-text search, 1280px previews in the UI,
React + TypeScript frontend, real Nuke `Read` node generation.

## Documentation

- [USAGE.md](USAGE.md) — full user guide (setup, UI, keyboard shortcuts,
  failure/recovery, API reference, troubleshooting)
- [CREDITS.md](CREDITS.md) — technology stack, dependency/license table,
  sources & references


All third-party dependencies are permissively licensed (see CREDITS.md).
