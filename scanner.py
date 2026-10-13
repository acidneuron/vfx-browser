"""Incremental scanner: walk roots, group image sequences, upsert assets."""
import os
import re
import threading
import time

import db
from media import VIDEO_EXTS, IMAGE_EXTS
from db import normp

SEQ_RE = re.compile(r"^(?P<base>.+?)[._](?P<num>\d{2,6})\.(?P<ext>[a-z0-9]+)$", re.I)
SKIP_DIRS = {".git", ".cache", "thumbs", "proxies", "previews", "cache", "$recycle.bin", "system volume information"}
MIN_SEQUENCE = 3

SCAN_STATE = {"running": False, "current": "", "found": 0, "started": 0.0}
_scan_lock = threading.Lock()


def _walk_files(root):
    files = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d.lower() not in SKIP_DIRS and not d.startswith(".")]
        for fn in filenames:
            if fn.startswith("."):
                continue
            ext = os.path.splitext(fn)[1].lower()
            if ext in VIDEO_EXTS | IMAGE_EXTS:
                files.append(normp(os.path.join(dirpath, fn)))
    return files


def _group_sequences(files):
    groups = {}
    singles = []
    for f in files:
        base = os.path.basename(f)
        d = os.path.dirname(f)
        m = SEQ_RE.match(base)
        ext = os.path.splitext(base)[1].lower()
        if ext in IMAGE_EXTS and m and len(m.group("num")) >= 2:
            key = (d, m.group("base"), ext, len(m.group("num")))
            groups.setdefault(key, []).append(f)
        else:
            singles.append(f)
    sequences = {}
    leftover = []
    for key, paths in groups.items():
        if len(paths) >= MIN_SEQUENCE:
            paths.sort(key=lambda p: int(SEQ_RE.match(os.path.basename(p)).group("num")))
            sequences[key] = paths
        else:
            leftover.extend(paths)
    seq_files = set(p for v in sequences.values() for p in v)
    return sequences, [f for f in singles if f not in seq_files] + leftover


def _category(root, path):
    try:
        rel = os.path.relpath(path, root)
    except ValueError:
        return ""
    parts = rel.split(os.sep)
    return parts[0].lower() if len(parts) > 1 else ""


def _upsert_video(root_id, root, path, jobs_q):
    st = os.stat(path)
    row = db.q("SELECT * FROM assets WHERE path=?", (path,), one=True)
    if row and row["missing"] == 0 and row["mtime"] == st.st_mtime and row["size"] == st.st_size:
        return False
    name = os.path.basename(path)
    ext = os.path.splitext(name)[1].lower().lstrip(".")
    cat = _category(root, path)
    if row:
        db.x("UPDATE assets SET root_id=?, kind='video', name=?, ext=?, size=?, mtime=?, missing=0, "
             "category=?, proxy_status='queued', proxy_error=NULL, last_indexed=datetime('now') WHERE id=?",
             (root_id, name, ext, st.st_size, st.st_mtime, cat, row["id"]))
        _queue_job(row["id"], jobs_q)
        return True
    db.x("INSERT INTO assets (root_id, path, kind, name, ext, size, mtime, category, proxy_status) "
         "VALUES (?,?,?,?,?,?,?,?,'queued')",
         (root_id, path, "video", name, ext, st.st_size, st.st_mtime, cat))
    aid = db.q("SELECT id FROM assets WHERE path=?", (path,), one=True)["id"]
    _queue_job(aid, jobs_q)
    return True


def _queue_job(asset_id, jobs_q):
    db.x("INSERT INTO jobs (asset_id, status) VALUES (?, 'queued')", (asset_id,))
    jobs_q.put(asset_id)


def _upsert_asset(root_id, root, path, kind, jobs_q, extra=None, stat_path=None):
    st = os.stat(stat_path or path)
    row = db.q("SELECT * FROM assets WHERE path=?", (path,), one=True)
    name = os.path.basename(path)
    ext = os.path.splitext(name)[1].lower().lstrip(".")
    cat = _category(root, path)
    extra = extra or {}
    if row:
        sets = ("UPDATE assets SET root_id=?, kind=?, name=?, ext=?, size=?, mtime=?, missing=0, "
                "category=?, proxy_status='queued', proxy_error=NULL, last_indexed=datetime('now')")
        for k, v in extra.items():
            sets += f", {k}=?"
        sets += " WHERE id=?"
        db.x(sets, (root_id, kind, name, ext, st.st_size, st.st_mtime, cat, *extra.values(), row["id"]))
        _queue_job(row["id"], jobs_q)
        return True
    cols = ["root_id", "path", "kind", "name", "ext", "size", "mtime", "category", "proxy_status"]
    vals = [root_id, path, kind, name, ext, st.st_size, st.st_mtime, cat, "queued"]
    for k, v in extra.items():
        cols.append(k)
        vals.append(v)
    ph = ",".join("?" * len(cols))
    db.x(f"INSERT INTO assets ({','.join(cols)}) VALUES ({ph})", vals)
    aid = db.q("SELECT id FROM assets WHERE path=?", (path,), one=True)["id"]
    _queue_job(aid, jobs_q)
    return True


def scan_root(root_id, root, jobs_q, log=print):
    if not os.path.isdir(root):
        log(f"scan: root not found: {root}")
        return {"status": "error", "error": f"root not found: {root}"}
    t0 = time.time()
    SCAN_STATE.update(running=True, current=f"scanning {root}", found=0, started=t0)
    try:
        files = _walk_files(root)
        SCAN_STATE["found"] = len(files)
        sequences, singles = _group_sequences(files)

        found_paths = set()
        n_new = 0
        for f in singles:
            if os.path.splitext(f)[1].lower() in VIDEO_EXTS:
                if _upsert_video(root_id, root, f, jobs_q):
                    n_new += 1
            else:
                if _upsert_asset(root_id, root, f, "image", jobs_q):
                    n_new += 1
            found_paths.add(f)

        for (d, base, ext, pad), paths in sequences.items():
            start = int(SEQ_RE.match(os.path.basename(paths[0])).group("num"))
            end = int(SEQ_RE.match(os.path.basename(paths[-1])).group("num"))
            expected = set(range(start, end + 1))
            have = {int(SEQ_RE.match(os.path.basename(p)).group("num")) for p in paths}
            missing_frames = len(expected - have)
            key_path = normp(os.path.join(d, f"{base}.%0{pad}d{ext}"))
            extra = {
                "frame_start": start, "frame_end": end, "padding": pad,
                "missing_frames": missing_frames, "frames": len(paths),
            }
            if _upsert_asset(root_id, root, key_path, "sequence", jobs_q, extra=extra, stat_path=paths[0]):
                n_new += 1
            found_paths.add(key_path)  # pattern path is the stored asset id
            for p in paths:
                found_paths.add(p)

        # mark missing (keep records per spec); chunked to stay under SQL var limits
        existing = [r["path"] for r in db.q("SELECT path FROM assets WHERE root_id=? AND missing=0", (root_id,))]
        missing = [p for p in existing if p not in found_paths]
        for i in range(0, len(missing), 900):
            chunk = missing[i:i + 900]
            ph = ",".join("?" * len(chunk))
            db.x(f"UPDATE assets SET missing=1 WHERE root_id=? AND path IN ({ph})", (root_id, *chunk))
        return {"status": "ok", "files": len(files), "sequences": len(sequences),
                "new_or_changed": n_new, "missing": len(missing), "seconds": round(time.time() - t0, 2)}
    finally:
        SCAN_STATE.update(running=False, current="")
