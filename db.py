"""SQLite catalogue for the VFX asset browser."""
import os
import re
import sqlite3

BASE = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE, "data", "vfx.db")

SCHEMA = """
CREATE TABLE IF NOT EXISTS roots (
  id INTEGER PRIMARY KEY,
  path TEXT UNIQUE NOT NULL,
  added_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS assets (
  id INTEGER PRIMARY KEY,
  root_id INTEGER,
  path TEXT UNIQUE NOT NULL,
  kind TEXT NOT NULL,              -- video | image | sequence
  name TEXT NOT NULL,
  ext TEXT,
  size INTEGER,
  mtime REAL,
  width INTEGER,
  height INTEGER,
  fps REAL,
  duration REAL,
  frames INTEGER,
  bit_depth INTEGER,
  has_alpha INTEGER,               -- 1 yes / 0 no / NULL unknown
  channels TEXT,
  codec TEXT,
  format TEXT,
  frame_start INTEGER,
  frame_end INTEGER,
  padding INTEGER,
  missing_frames INTEGER,
  category TEXT,
  tags TEXT DEFAULT '',
  notes TEXT DEFAULT '',
  favorite INTEGER DEFAULT 0,
  thumb_path TEXT,
  proxy_path TEXT,
  preview_path TEXT,
  proxy_status TEXT DEFAULT 'none', -- none|queued|running|ready|failed
  proxy_error TEXT,
  missing INTEGER DEFAULT 0,
  first_seen TEXT DEFAULT (datetime('now')),
  last_indexed TEXT DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_assets_kind ON assets(kind);
CREATE INDEX IF NOT EXISTS idx_assets_name ON assets(name);
CREATE INDEX IF NOT EXISTS idx_assets_category ON assets(category);
CREATE INDEX IF NOT EXISTS idx_assets_root ON assets(root_id);

CREATE TABLE IF NOT EXISTS jobs (
  id INTEGER PRIMARY KEY,
  asset_id INTEGER NOT NULL,
  status TEXT DEFAULT 'queued',     -- queued|running|done|failed
  attempts INTEGER DEFAULT 0,
  error TEXT,
  created_at TEXT DEFAULT (datetime('now')),
  updated_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_jobs_asset ON jobs(asset_id);

CREATE VIRTUAL TABLE IF NOT EXISTS assets_fts USING fts5(
  name, tags, content='assets', content_rowid='id'
);
CREATE TRIGGER IF NOT EXISTS assets_ai AFTER INSERT ON assets BEGIN
  INSERT INTO assets_fts(rowid, name, tags) VALUES (new.id, new.name, new.tags);
END;
CREATE TRIGGER IF NOT EXISTS assets_ad AFTER DELETE ON assets BEGIN
  INSERT INTO assets_fts(assets_fts, rowid, name, tags) VALUES ('delete', old.id, old.name, old.tags);
END;
CREATE TRIGGER IF NOT EXISTS assets_au AFTER UPDATE ON assets BEGIN
  INSERT INTO assets_fts(assets_fts, rowid, name, tags) VALUES ('delete', old.id, old.name, old.tags);
  INSERT INTO assets_fts(rowid, name, tags) VALUES (new.id, new.name, new.tags);
END;
"""


def get_db() -> sqlite3.Connection:
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH, check_same_thread=False, timeout=15)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def init_db():
    conn = get_db()
    conn.executescript(SCHEMA)
    conn.commit()
    conn.close()


def q(sql, args=(), one=False):
    conn = get_db()
    try:
        cur = conn.execute(sql, args)
        rows = cur.fetchall()
        if one:
            return rows[0] if rows else None
        return rows
    finally:
        conn.close()


def x(sql, args=()):
    conn = get_db()
    try:
        cur = conn.execute(sql, args)
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


def row_to_dict(row):
    if row is None:
        return None
    d = dict(row)
    return d


def normp(p: str) -> str:
    """Canonical path form for storage (forward slashes, normalized)."""
    return os.path.normpath(p).replace("\\", "/")


SEQ_PLACEHOLDER = re.compile(r"\.%0(\d+)d$")


def seq_split(pattern_path: str):
    """(dir, base_name, ext, pad) from a stored sequence pattern 'dir/base.%04d.ext'."""
    d = os.path.dirname(pattern_path)
    base, ext = os.path.splitext(pattern_path)
    m = SEQ_PLACEHOLDER.search(base)
    if m:
        return d, base[: m.start()], ext, int(m.group(1))
    return d, base, ext, 4


def seq_frame(pattern_path: str, num: int) -> str:
    """Real file path for a frame number of a stored sequence pattern."""
    d, base, ext, pad = seq_split(pattern_path)
    return os.path.join(d, f"{base}.{num:0{pad}d}{ext}")


def seq_nuke_path(pattern_path: str) -> str:
    d, base, ext, pad = seq_split(pattern_path)
    return f"{os.path.join(d, base)}.%0{pad}d{ext}"
