# VFX Asset Browser — References & Credits

**Version:** Stage 01 (local-first asset browser)
**Date:** 2026-10-12
**Platform:** Windows 11 · Python 3.12.9


A local-first VFX asset browser (FootageCrate-style): indexes existing media folders,
generates near-instant thumbnails/proxies, and provides search/filter, metadata,
favorites, and Nuke-ready paths — without ever modifying the originals.

---

## 1. Technology Stack

| Component | Technology | Notes |
|---|---|---|
| Backend | Python 3.12.9 + FastAPI 0.143.0 | HTTP API on `http://127.0.0.1:8765` |
| ASGI server | Uvicorn 0.54.0 | `uvicorn[standard]` (httptools, websockets, watchfiles) |
| Database | SQLite 3 (CPython stdlib `sqlite3`) | Single file `data/vfx.db` — no server, no install |
| File watching | watchdog 6.0.0 | Background root monitoring → incremental rescan |
| Media probing | FFmpeg / ffprobe n9.0.1 | Metadata (duration, fps, codecs, alpha) |
| Derivatives | FFmpeg (libx264) | Thumbnails, proxies, checkerboard alpha compositing |
| EXR parsing | Native Python reader (original code) | Implements the OpenEXR file layout spec |
| Frontend | Vanilla HTML / CSS / JS (no build step) | Dark VFX theme, responsive card grid |
| Packaging | `uv` (venv + dependency resolution) | `requirements.txt` pins the direct deps |

**Direct dependencies** (declared in `requirements.txt`): `fastapi`, `uvicorn[standard]`, `watchdog`.
Everything else is a transitive dependency of those three.

**No external application code was copied.** The entire application (backend, frontend,
EXR parser, scanner, job pipeline) is original code. The only external runtime
requirement is the FFmpeg/ffprobe binary suite.

---

## 2. Python Dependencies (verified from venv metadata, 2026-10-12)

| Package | Version | License | Role |
|---|---|---|---|
| fastapi | 0.143.0 | MIT | Web framework / API layer |
| starlette | 1.7.0 | BSD-3-Clause | ASGI core (via FastAPI) |
| pydantic | 2.14.0 | MIT | Request/response validation |
| pydantic-core | 2.50.0 | MIT | Pydantic Rust core |
| uvicorn | 0.54.0 | BSD-3-Clause | ASGI server |
| click | 8.5.0 | BSD-3-Clause | uvicorn CLI |
| h11 | 0.16.0 | MIT | HTTP/1.1 protocol (uvicorn) |
| httptools | 0.9.0 | MIT | Fast HTTP parsing (uvicorn standard) |
| websockets | 17.2 | BSD-3-Clause | WebSocket support (uvicorn standard) |
| watchfiles | 1.3.0 | MIT | Native file watching (uvicorn standard) |
| anyio | 4.15.1 | MIT | Async compatibility layer |
| watchdog | 6.0.0 | Apache-2.0 | Root folder monitoring |
| python-dotenv | 1.2.4 | BSD-3-Clause | Env loading (uvicorn standard) |
| pyyaml | 6.0.3 | MIT | Config parsing (uvicorn standard) |
| idna | 3.20 | BSD-3-Clause | IDNA handling (anyio) |
| typing-extensions | 4.16.0 | PSF-2.0 | Typing backports |
| annotated-types | 0.0.5 | MIT | Pydantic annotations |
| annotated-doc | 0.0.5 | MIT | Pydantic doc annotations |
| typing-inspection | 0.4.4 | MIT | Pydantic typing utils |
| opentelemetry-api | 1.45.1 | Apache-2.0 | Request telemetry instrumentation built into FastAPI 0.143+ (no-op unless a provider is configured) |

All licenses are OSI-approved permissive licenses (MIT / BSD-3-Clause / Apache-2.0 / PSF-2.0).

---

## 3. External Tools

| Tool | Version | License | Used for |
|---|---|---|---|
| FFmpeg / ffprobe | n9.0.1-11-ge47273f4d9-20260831 (built 2026-08-31) | GPL-3.0+ / LGPL-2.1+ | Metadata probing (`ffprobe` JSON), thumbnail/proxy generation (`ffmpeg`, libx264, `filter_complex` alpha compositing over checkerboard) |
| Python | 3.12.9 | PSF License | Runtime |
| SQLite | bundled with CPython | Public domain | Catalog storage |

FFmpeg is invoked as an external process; it is **not** linked into the Python code,
so its GPL terms apply to the FFmpeg binary itself, not to this application's code.

---

## 4. Sources & References

| Source | Used for |
|---|---|
| OpenEXR File Layout specification — <https://openexr.com/documentation/openexr-file-layout.pdf> | Native EXR header parser in `media.py` (magic `0x762f3101`, version, null-terminated attribute names, channel entries with strides `j+11`/`j+17`). No EXR library dependency — dimensions, bit depth, channel list, and alpha detection read directly from the file header. |
| FFmpeg documentation — <https://ffmpeg.org/documentation.html> | `image2` demuxer with `-framerate` / `-start_number` for sequence proxies, `filter_complex` scale + overlay for alpha checkerboard previews, `libx264 -preset veryfast -crf 30`, `-movflags +faststart` for instant web playback. |
| FastAPI documentation — <https://fastapi.tiangolo.com> | API design, `FileResponse`, Pydantic request models. |
| Python documentation — <https://docs.python.org/3/library/sqlite3.html> | Catalog schema, parameterized queries, `datetime('now')` timestamps. |
| Windows ShellExecute documentation — <https://learn.microsoft.com/windows/win32/api/shellapi/nf-shellapi-shellexecutew> | "Reveal in Explorer": `ShellExecuteW(None, "open", dir, "/select,name", None, 1)` opens Explorer with the file selected. |

---

## 5. Architecture Summary

```
vfx-browser/
├── main.py      # FastAPI app, API routes, watchdog, asset actions
├── db.py        # SQLite schema + canonical path/sequence helpers
├── media.py     # ffprobe wrapper + native PNG/EXR readers
├── proxy.py     # Thumbnail / preview / proxy generation (FFmpeg)
├── scanner.py   # Incremental root scan, sequence grouping, missing detection
├── jobs.py      # Background job queue (2 workers): metadata → thumb → proxy
├── static/      # Vanilla HTML/CSS/JS dark UI
├── data/        # SQLite database (vfx.db) — app state
├── cache/       # Derived files only: thumbs/, proxies/, previews/, checker.png
├── test_library/  # Synthetic test assets
├── requirements.txt
└── run.bat
```

Design principles: originals are never touched; all derived artifacts stay under
`cache/`; sequences are stored as one pattern row (`dir/base.%04d.ext`); all stored
paths are canonical (forward slashes); failed probes yield `has_alpha = NULL`
(unknown), never a false "no alpha".

---

## 6. Credits

- **Concept, specification, and direction** — ACIDNEURON (project owner)
- **Libraries** — as listed in §2 (all permissively licensed)
- **FFmpeg project** — <http://ffmpeg.org> (GPL/LGPL), the de-facto standard for
  multimedia probing and transcoding

