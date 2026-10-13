"""VFX Asset Browser — local first, FastAPI backend.

Run:  python main.py   (serves http://127.0.0.1:8765)
"""
import os
import re
import subprocess
import threading
import time

import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel

import db
import jobs as jobs_mod
import media
import scanner
import proxy as px

BASE = os.path.dirname(os.path.abspath(__file__))
STATIC = os.path.join(BASE, "static")
PORT = 8765

app = FastAPI(title="VFX Asset Browser")
jq = jobs_mod.JobQueue(workers=2)


@app.get("/static/{path:path}")
def static_file(path: str):
    full = os.path.normpath(os.path.join(STATIC, path))
    if not full.startswith(STATIC) or not os.path.isfile(full):
        raise HTTPException(404, "not found")
    return FileResponse(full)

# ---------------------------------------------------------------- roots


class RootIn(BaseModel):
    path: str


class ActionIn(BaseModel):
    action: str


class AssetUpdate(BaseModel):
    tags: str | None = None
    notes: str | None = None
    favorite: int | None = None
    fps: float | None = None


def add_root(path: str) -> int:
    path = db.normp(os.path.abspath(os.path.expandvars(os.path.expanduser(path))))
    if not os.path.isdir(path):
        raise HTTPException(400, f"not a directory: {path}")
    row = db.q("SELECT id FROM roots WHERE path=?", (path,), one=True)
    if row:
        return row["id"]
    return db.x("INSERT INTO roots (path) VALUES (?)", (path,))


def start_scan(root_id, root_path):
    def run():
        scanner.scan_root(root_id, root_path, jq.q)
    threading.Thread(target=run, daemon=True).start()


# ---------------------------------------------------------------- API


@app.get("/api/roots")
def api_roots():
    rows = db.q("""SELECT r.*, (SELECT COUNT(*) FROM assets a WHERE a.root_id=r.id) n_assets,
                          (SELECT COUNT(*) FROM assets a WHERE a.root_id=r.id AND a.missing=1) n_missing
                   FROM roots r ORDER BY r.path""")
    return [dict(r) for r in rows]


@app.post("/api/roots")
def api_add_root(body: RootIn):
    rid = add_root(body.path)
    start_scan(rid, body.path)
    return {"ok": True, "root_id": rid}


@app.delete("/api/roots/{rid}")
def api_del_root(rid: int):
    row = db.q("SELECT path FROM roots WHERE id=?", (rid,), one=True)
    if not row:
        raise HTTPException(404, "root not found")
    # 1. Close in-flight jobs for this root's assets
    db.x("UPDATE jobs SET status='done', updated_at=datetime('now') "
         "WHERE status IN ('queued','running') "
         "AND asset_id IN (SELECT id FROM assets WHERE root_id=?)", (rid,))
    # 2. Remove cached derivatives (derived files only — originals are untouched)
    for r in db.q("SELECT thumb_path, proxy_path, preview_path FROM assets WHERE root_id=?", (rid,)):
        for k in ("thumb_path", "proxy_path", "preview_path"):
            p = r[k]
            if p and os.path.isfile(p):
                try:
                    os.remove(p)
                except OSError:
                    pass
    # 3. Delete jobs, then assets (FTS triggers clean the index rows)
    db.x("DELETE FROM jobs WHERE asset_id IN (SELECT id FROM assets WHERE root_id=?)", (rid,))
    n = db.q("SELECT COUNT(*) n FROM assets WHERE root_id=?", (rid,), one=True)["n"]
    db.x("DELETE FROM assets WHERE root_id=?", (rid,))
    # 4. Delete the root row
    db.x("DELETE FROM roots WHERE id=?", (rid,))
    # 5. Stop watching the folder
    _unschedule_root(row["path"])
    return {"ok": True, "removed_assets": n}


@app.post("/api/roots/{rid}/rescan")
def api_rescan(rid: int):
    row = db.q("SELECT path FROM roots WHERE id=?", (rid,), one=True)
    if not row:
        raise HTTPException(404, "root not found")
    start_scan(rid, row["path"])
    return {"ok": True}


@app.get("/api/status")
def api_status():
    total = db.q("SELECT COUNT(*) n FROM assets", one=True)["n"]
    missing = db.q("SELECT COUNT(*) n FROM assets WHERE missing=1", one=True)["n"]
    ready = db.q("SELECT COUNT(*) n FROM assets WHERE proxy_status='ready'", one=True)["n"]
    return {
        "assets": total, "missing": missing, "ready": ready,
        "jobs": jobs_mod.job_counts(), "queue": jq.pending(),
        "scan": dict(scanner.SCAN_STATE),
        "roots": [dict(r) for r in db.q("SELECT id, path FROM roots")],
    }


@app.post("/api/jobs/retry-failed")
def api_retry_failed():
    n = jobs_mod.retry_failed(jq)
    return {"retried": n}


def _filter_sql(q_, kind, format_, alpha, min_w, max_h, tag, fav, sort, page, limit):
    where, args = ["a.missing=0"], []
    if q_:
        where.append("(a.name LIKE ? OR a.tags LIKE ? OR a.category LIKE ?)")
        args += [f"%{q_}%", f"%{q_}%", f"%{q_}%"]
    if kind:
        where.append("a.kind=?")
        args.append(kind)
    if format_:
        where.append("a.ext=?")
        args.append(format_.lower().lstrip("."))
    if alpha == "yes":
        where.append("a.has_alpha=1")
    elif alpha == "no":
        where.append("a.has_alpha=0")
    elif alpha == "unknown":
        where.append("a.has_alpha IS NULL")
    if min_w:
        where.append("(a.width IS NULL OR a.width>=?)")
        args.append(int(min_w))
    if tag:
        where.append("a.tags LIKE ?")
        args.append(f"%{tag}%")
    if fav:
        where.append("a.favorite=1")
    sql = f"SELECT a.*, (SELECT r.path FROM roots r WHERE r.id=a.root_id) root_path FROM assets a WHERE {' AND '.join(where)} "
    order = {"name": "a.name COLLATE NOCASE", "size": "a.size", "mtime": "a.mtime",
             "duration": "COALESCE(a.duration,0)", "recent": "a.first_seen"}.get(sort, "a.name COLLATE NOCASE")
    sql += f" ORDER BY {order} DESC LIMIT ? OFFSET ?"
    args += [limit, (page - 1) * limit]
    return sql, args


@app.get("/api/assets")
def api_assets(q_: str = "", kind: str = "", format: str = "", alpha: str = "",
               min_w: int = 0, tag: str = "", fav: int = 0, show_missing: int = 0,
               category: str = "", root: int = 0, missing_only: int = 0,
               sort: str = "recent", page: int = 1, limit: int = 240):
    where, args = [], []
    if missing_only:
        where.append("a.missing=1")
    elif not show_missing:
        where.append("a.missing=0")
    if q_:
        where.append("(a.name LIKE ? OR a.tags LIKE ? OR a.category LIKE ?)")
        args += [f"%{q_}%", f"%{q_}%", f"%{q_}%"]
    if kind:
        where.append("a.kind=?")
        args.append(kind)
    if format:
        where.append("a.ext=?")
        args.append(format.lower().lstrip("."))
    if alpha == "yes":
        where.append("a.has_alpha=1")
    elif alpha == "no":
        where.append("a.has_alpha=0")
    elif alpha == "unknown":
        where.append("a.has_alpha IS NULL")
    if min_w:
        where.append("(a.width IS NULL OR a.width>=?)")
        args.append(min_w)
    if tag:
        where.append("a.tags LIKE ?")
        args.append(f"%{tag}%")
    if category:
        where.append("a.category=?")
        args.append(category)
    if root:
        where.append("a.root_id=?")
        args.append(root)
    if fav:
        where.append("a.favorite=1")
    w = f" WHERE {' AND '.join(where)}" if where else ""
    total = db.q(f"SELECT COUNT(*) n FROM assets a{w}", args, one=True)["n"]
    order = {"name": "a.name COLLATE NOCASE", "size": "a.size", "mtime": "a.mtime",
             "duration": "COALESCE(a.duration,0)", "recent": "a.first_seen"}.get(sort, "a.first_seen")
    rows = db.q(f"SELECT a.*, (SELECT r.path FROM roots r WHERE r.id=a.root_id) root_path "
                f"FROM assets a{w} ORDER BY {order} DESC LIMIT ? OFFSET ?",
                args + [limit, (page - 1) * limit])
    return {"total": total, "page": page, "items": [dict(r) for r in rows]}


@app.get("/api/asset/{aid}")
def api_asset(aid: int):
    row = db.q("SELECT a.*, (SELECT r.path FROM roots r WHERE r.id=a.root_id) root_path "
               "FROM assets a WHERE a.id=?", (aid,), one=True)
    if not row:
        raise HTTPException(404, "asset not found")
    return dict(row)


@app.post("/api/asset/{aid}")
def api_update_asset(aid: int, body: AssetUpdate):
    row = db.q("SELECT * FROM assets WHERE id=?", (aid,), one=True)
    if not row:
        raise HTTPException(404, "asset not found")
    sets, args = [], []
    for field in ("tags", "notes", "favorite", "fps"):
        v = getattr(body, field)
        if v is not None:
            sets.append(f"{field}=?")
            args.append(v)
    if sets:
        db.x(f"UPDATE assets SET {', '.join(sets)} WHERE id=?", (*args, aid))
    return dict(db.q("SELECT a.*, (SELECT r.path FROM roots r WHERE r.id=a.root_id) root_path FROM assets a WHERE a.id=?", (aid,), one=True))


@app.get("/api/asset/{aid}/thumb")
def api_thumb(aid: int):
    row = db.q("SELECT thumb_path, proxy_status FROM assets WHERE id=?", (aid,), one=True)
    if not row:
        raise HTTPException(404)
    if row["thumb_path"] and os.path.exists(row["thumb_path"]):
        return FileResponse(row["thumb_path"], media_type="image/jpeg")
    return JSONResponse({"status": row["proxy_status"]}, status_code=202)


@app.get("/api/asset/{aid}/proxy")
def api_proxy(aid: int):
    row = db.q("SELECT proxy_path, proxy_status, preview_path FROM assets WHERE id=?", (aid,), one=True)
    if not row:
        raise HTTPException(404)
    p = row["proxy_path"] or row["preview_path"]
    if p and os.path.exists(p):
        mt = "video/mp4" if p.endswith(".mp4") else "image/png"
        return FileResponse(p, media_type=mt)
    return JSONResponse({"status": row["proxy_status"]}, status_code=202)


@app.get("/api/asset/{aid}/preview")
def api_preview(aid: int):
    row = db.q("SELECT preview_path, thumb_path, proxy_status FROM assets WHERE id=?", (aid,), one=True)
    if not row:
        raise HTTPException(404)
    p = row["preview_path"] or row["thumb_path"]
    if p and os.path.exists(p):
        mt = "image/png" if p.endswith(".png") else "image/jpeg"
        return FileResponse(p, media_type=mt)
    return JSONResponse({"status": row["proxy_status"]}, status_code=202)


def nuke_path_for(row) -> str:
    if row["kind"] == "sequence":
        return db.seq_nuke_path(row["path"])
    return row["path"]


@app.post("/api/asset/{aid}/action")
def api_action(aid: int, body: ActionIn):
    row = db.q("SELECT * FROM assets WHERE id=?", (aid,), one=True)
    if not row:
        raise HTTPException(404, "asset not found")
    a = body.action
    if row["kind"] == "sequence":
        real = db.seq_frame(row["path"], int(row["frame_start"] or 1))
    else:
        real = row["path"]
    if a == "copy_path":
        return {"ok": True, "data": row["path"] if row["kind"] == "sequence" else real}
    if a == "nuke_path":
        return {"ok": True, "data": nuke_path_for(row)}
    if a == "fav":
        new = 1 - int(row["favorite"] or 0)
        db.x("UPDATE assets SET favorite=? WHERE id=?", (new, aid))
        return {"ok": True, "favorite": new}
    if a == "reveal":
        # ShellExecute: open parent folder with the file selected.
        # (os.startfile's 2nd arg is the VERB, not parameters — it can't do /select.)
        import ctypes
        h = ctypes.windll.shell32.ShellExecuteW(
            None, "open", os.path.dirname(real), f"/select,{os.path.basename(real)}", None, 1)
        if h <= 32:
            raise HTTPException(500, f"reveal failed (WinError {int(h)})")
        return {"ok": True}
    if a == "open":
        os.startfile(real)  # noqa: S606
        return {"ok": True}
    if a == "reprocess":
        px.clear_cache_files(aid)
        db.x("UPDATE assets SET proxy_status='queued', proxy_error=NULL, thumb_path=NULL, proxy_path=NULL, preview_path=NULL WHERE id=?", (aid,))
        db.x("INSERT INTO jobs (asset_id, status) VALUES (?, 'queued')", (aid,))
        jq.put(aid)
        return {"ok": True}
    raise HTTPException(400, f"unknown action {a}")


@app.get("/api/facets")
def api_facets():
    formats = db.q("SELECT ext, COUNT(*) n FROM assets WHERE missing=0 AND ext IS NOT NULL GROUP BY ext ORDER BY n DESC")
    cats = db.q("SELECT category, COUNT(*) n FROM assets WHERE missing=0 AND category<>'' GROUP BY category ORDER BY n DESC")
    return {"formats": [dict(r) for r in formats], "categories": [dict(r) for r in cats]}


# ---------------------------------------------------------------- watchdog

_observer = None


def _unschedule_root(path: str):
    """Stop watching one root folder (best-effort)."""
    if _observer is None:
        return
    try:
        _observer.unschedule(path)
    except Exception:
        pass


def _watch_roots():
    global _observer
    try:
        from watchdog.observers import Observer
        from watchdog.events import FileSystemEventHandler
    except ImportError:
        return
    roots = db.q("SELECT id, path FROM roots")
    if not roots:
        return
    last = {}

    class _Handler(FileSystemEventHandler):
        def on_any_event(self, event):
            if event.is_directory:
                return
            p = event.src_path
            for r in roots:
                if p.lower().startswith(r["path"].lower() + os.sep):
                    now = time.time()
                    if now - last.get(r["id"], 0) > 5:
                        last[r["id"]] = now
                        threading.Thread(
                            target=lambda: scanner.scan_root(r["id"], r["path"], jq.q),
                            daemon=True).start()
                    break

    observer = Observer()
    for r in roots:
        if os.path.isdir(r["path"]):
            observer.schedule(_Handler(), r["path"], recursive=True)
    observer.start()
    _observer = observer


# ---------------------------------------------------------------- boot

db.init_db()
jobs_mod.recover_interrupted(jq)


@app.on_event("startup")
def boot():
    threading.Thread(target=_watch_roots, daemon=True).start()


@app.get("/")
def index():
    return FileResponse(os.path.join(STATIC, "index.html"))


if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=PORT, log_level="warning")
    print(f"VFX Asset Browser: http://127.0.0.1:{PORT}")
