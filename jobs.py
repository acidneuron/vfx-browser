"""Background job pipeline: meta -> thumbnail -> proxy, bounded workers."""
import os
import queue
import threading
import time
import traceback

import db
import media
import proxy as px


class JobQueue:
    def __init__(self, workers=2):
        self.q = queue.Queue()
        self.inflight = set()
        self._inflight_lock = threading.Lock()
        self.workers = workers
        self._threads = []
        self._stop = False
        for _ in range(workers):
            t = threading.Thread(target=self._worker, daemon=True, name="vfx-worker")
            t.start()
            self._threads.append(t)

    def put(self, asset_id):
        """Enqueue an asset, ignoring duplicates already in flight."""
        with self._inflight_lock:
            if asset_id in self.inflight:
                return False
            self.inflight.add(asset_id)
        self.q.put(asset_id)
        return True

    def stop(self):
        self._stop = True

    def pending(self):
        return self.q.qsize()

    def _worker(self):
        while not self._stop:
            try:
                asset_id = self.q.get(timeout=1)
            except queue.Empty:
                continue
            try:
                job = db.q("SELECT * FROM jobs WHERE asset_id=? ORDER BY id DESC LIMIT 1", (asset_id,), one=True)
                if job is not None:
                    self._process(job["id"], asset_id)
            finally:
                with self._inflight_lock:
                    self.inflight.discard(asset_id)
                self.q.task_done()

    def _set_job(self, job_id, status, error=None):
        db.x("UPDATE jobs SET status=?, error=?, updated_at=datetime('now'), attempts=attempts+1 WHERE id=?",
             (status, error, job_id))

    def _set_asset_status(self, asset_id, status, error=None):
        db.x("UPDATE assets SET proxy_status=?, proxy_error=? WHERE id=?", (status, error, asset_id))

    def _close_stale(self, asset_id, status, error=None):
        """Close any other queued/running job rows for this asset (double-queue race)."""
        db.x("UPDATE jobs SET status=?, error=?, updated_at=datetime('now') "
             "WHERE asset_id=? AND status IN ('queued','running')", (status, error, asset_id))

    def _process(self, job_id, asset_id):
        asset_row = db.q("SELECT * FROM assets WHERE id=?", (asset_id,), one=True)
        if asset_row is None:
            self._set_job(job_id, "failed", "asset deleted")
            self._close_stale(asset_id, "failed", "asset deleted")
            return
        asset = dict(asset_row)
        self._set_job(job_id, "running")
        self._close_stale(asset_id, "running")
        self._set_asset_status(asset_id, "running")
        try:
            self._process_asset(asset)
            self._set_job(job_id, "done")
        except media.MediaError as e:
            self._set_job(job_id, "failed", str(e))
            self._set_asset_status(asset_id, "failed", str(e))
            self._close_stale(asset_id, "failed", str(e))
        except Exception as e:  # noqa: BLE001
            self._set_job(job_id, "failed", f"{e}\n{traceback.format_exc()[-300:]}")
            self._set_asset_status(asset_id, "failed", str(e)[:300])
            self._close_stale(asset_id, "failed", str(e)[:300])

    def _process_asset(self, asset):
        kind = asset["kind"]
        path = asset["path"]

        if kind == "sequence":
            first = db.seq_frame(path, int(asset["frame_start"] or 1))
            if not os.path.exists(first):
                raise media.MediaError(f"sequence first frame missing: {first}")
            src = first
            is_video = False
        else:
            src = path
            if not os.path.exists(src):
                raise media.MediaError(f"source missing: {src}")
            is_video = kind == "video"

        # 1) metadata
        meta = media.probe(src, kind if kind != "sequence" else "image")
        has_alpha = meta.get("has_alpha")
        fps = meta.get("fps")
        duration = meta.get("duration")
        frames = None
        if kind == "sequence":
            fps = fps or 25.0
            frames = asset["frames"] or 0
            duration = frames / fps if fps else None
            # sequence width/height come from the sampled first frame
        db.x(
            "UPDATE assets SET width=?, height=?, fps=?, duration=?, frames=?, bit_depth=?, "
            "has_alpha=?, channels=?, codec=?, format=? WHERE id=?",
            (meta.get("width"), meta.get("height"), round(fps, 3) if fps else None,
             round(duration, 3) if duration is not None else None, frames,
             meta.get("bit_depth"), has_alpha, meta.get("channels"),
             meta.get("codec"), meta.get("format"), asset["id"]))

        # 2) thumbnail
        thumb = px.make_thumb(asset["id"], src, bool(has_alpha), is_video or kind == "sequence")
        # 3) proxy / preview
        if kind == "image":
            prev = px.make_preview(asset["id"], src, bool(has_alpha))
            db.x("UPDATE assets SET thumb_path=?, preview_path=?, proxy_status='ready', proxy_error=NULL WHERE id=?",
                 (thumb, prev, asset["id"]))
        else:
            # proxy for sequences must consume the pattern, not a single frame
            src_proxy = path if kind == "sequence" else src
            pr = px.make_proxy(asset["id"], src_proxy, kind, bool(has_alpha),
                               fps=fps or 25.0, start_number=asset.get("frame_start"))
            db.x("UPDATE assets SET thumb_path=?, proxy_path=?, proxy_status='ready', proxy_error=NULL WHERE id=?",
                 (thumb, pr, asset["id"]))


def recover_interrupted(jq):
    """Re-queue assets left in 'running'/'queued' state from a previous session."""
    rows = db.q("SELECT id FROM assets WHERE proxy_status IN ('running','queued')")
    for r in rows:
        db.x("UPDATE assets SET proxy_status='queued', proxy_error=NULL WHERE id=?", (r["id"],))
        db.x("INSERT INTO jobs (asset_id, status) VALUES (?, 'queued')", (r["id"],))
        jq.put(r["id"])
    return len(rows)


def retry_failed(jq, max_attempts=3):
    """Re-queue assets in 'failed' status, capped at max_attempts per asset."""
    rows = db.q(
        "SELECT a.id, (SELECT attempts FROM jobs j WHERE j.asset_id=a.id "
        "ORDER BY j.id DESC LIMIT 1) att FROM assets a WHERE a.proxy_status='failed'")
    n = 0
    for r in rows:
        if (r["att"] or 0) >= max_attempts:
            continue
        db.x("UPDATE assets SET proxy_status='queued', proxy_error=NULL WHERE id=?", (r["id"],))
        db.x("INSERT INTO jobs (asset_id, status) VALUES (?, 'queued')", (r["id"],))
        jq.put(r["id"])
        n += 1
    return n


def job_counts():
    rows = db.q("SELECT status, COUNT(*) n FROM jobs GROUP BY status")
    d = {r["status"]: r["n"] for r in rows}
    return d
