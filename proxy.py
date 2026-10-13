"""Proxy + thumbnail generation with FFmpeg. Originals are never touched."""
import os
import struct
import subprocess
import zlib

from media import FFMPEG, MediaError

BASE = os.path.dirname(os.path.abspath(__file__))
CACHE = os.path.join(BASE, "cache")
THUMBS = os.path.join(CACHE, "thumbs")
PROXIES = os.path.join(CACHE, "proxies")
PREVIEWS = os.path.join(CACHE, "previews")
CHECKER = os.path.join(CACHE, "checker.png")

THUMB_W = 480
PREVIEW_W = 1280
PROXY_W = 480
PROXY_SECONDS = 4.0


def _mkdirs():
    for d in (CACHE, THUMBS, PROXIES, PREVIEWS):
        os.makedirs(d, exist_ok=True)


def _write_png(path, w, h, rows_rgb):
    def chunk(t, d):
        c = t + d
        return struct.pack(">I", len(d)) + c + struct.pack(">I", zlib.crc32(c) & 0xFFFFFFFF)
    raw = b"".join(b"\x00" + r for r in rows_rgb)
    png = b"\x89PNG\r\n\x1a\n"
    png += chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
    png += chunk(b"IDAT", zlib.compress(raw, 6))
    png += chunk(b"IEND", b"")
    with open(path, "wb") as f:
        f.write(png)


def ensure_checker():
    _mkdirs()
    if os.path.exists(CHECKER):
        return CHECKER
    n = 512
    cell = 16
    rows = []
    for y in range(n):
        row = bytearray()
        for x in range(n):
            c = bytes((200, 200, 200)) if ((x // cell + y // cell) % 2 == 0) else bytes((96, 96, 96))
            row += c
        rows.append(bytes(row))
    _write_png(CHECKER, n, n, rows)
    return CHECKER


def _run(cmd):
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
    if r.returncode != 0:
        raise MediaError((r.stderr or "ffmpeg failed").strip()[-400:])
    return r


def _alpha_composite(args_in, out, scale):
    """Compose a transparent source over the checkerboard, output to `out`."""
    ensure_checker()
    vf = (
        f"[0:v]scale='min({scale},iw)':'-2'[s];"
        f"[1:v]scale='min({scale},iw)':'-2'[c];"
        f"[c][s]overlay=0:0,format=rgb24{out}"
    )
    _run([FFMPEG, "-hide_banner", "-loglevel", "error", "-y",
          *args_in, "-filter_complex", vf, "-frames:v", "1", out])


def make_thumb(asset_id, src, has_alpha, is_video):
    _mkdirs()
    out = os.path.join(THUMBS, f"{asset_id}.jpg")
    if os.path.exists(out) and os.path.getsize(out) > 0:
        return out
    if has_alpha:
        inp = ["-i", src, "-i", ensure_checker()] if is_video else ["-i", src, "-i", ensure_checker()]
        vf = (
            f"[0:v]scale='min({THUMB_W},iw)':'-2'[s];"
            f"[1:v]scale='min({THUMB_W},iw)':'-2'[c];"
            f"[c][s]overlay=0:0,format=rgb24"
        )
        _run([FFMPEG, "-hide_banner", "-loglevel", "error", "-y",
              *inp, "-frames:v", "1", "-filter_complex", vf, "-q:v", "4", out])
    else:
        _run([FFMPEG, "-hide_banner", "-loglevel", "error", "-y",
              "-i", src, "-frames:v", "1",
              "-vf", f"scale='min({THUMB_W},iw)':'-2'", "-q:v", "4", out])
    if not os.path.exists(out):
        raise MediaError("thumbnail not produced")
    return out


def make_proxy(asset_id, src, kind, has_alpha, fps=25.0, start_number=None):
    """Short H.264 preview (mp4) for video/sequence. Returns path."""
    _mkdirs()
    out = os.path.join(PROXIES, f"{asset_id}.mp4")
    if os.path.exists(out) and os.path.getsize(out) > 0:
        return out
    base = [FFMPEG, "-hide_banner", "-loglevel", "error", "-y"]

    def _inputs():
        if kind == "sequence":
            args = ["-f", "image2", "-framerate", str(int(fps or 25))]
            if start_number:
                args += ["-start_number", str(start_number)]
            return args + ["-i", src]
        return ["-i", src]

    if has_alpha:
        vf = (
            f"[0:v]scale='min({PROXY_W},iw)':'-2'[s];"
            f"[1:v]scale='min({PROXY_W},iw)':'-2'[c];"
            f"[c][s]overlay=0:0,format=yuv420p"
        )
        _run(base + _inputs() + ["-i", ensure_checker(),
              "-t", str(PROXY_SECONDS), "-filter_complex", vf,
              "-c:v", "libx264", "-preset", "veryfast", "-crf", "30",
              "-pix_fmt", "yuv420p", "-movflags", "+faststart", out])
    else:
        _run(base + _inputs() + ["-t", str(PROXY_SECONDS), "-an",
              "-vf", f"scale='min({PROXY_W},iw)':'-2'",
              "-c:v", "libx264", "-preset", "veryfast", "-crf", "30",
              "-pix_fmt", "yuv420p", "-movflags", "+faststart", out])
    if not os.path.exists(out) or os.path.getsize(out) == 0:
        raise MediaError("proxy not produced")
    return out


def make_preview(asset_id, src, has_alpha):
    """Full-ish still preview (png, 1280w) for single images."""
    _mkdirs()
    out = os.path.join(PREVIEWS, f"{asset_id}.png")
    if os.path.exists(out) and os.path.getsize(out) > 0:
        return out
    if has_alpha:
        vf = (
            f"[0:v]scale='min({PREVIEW_W},iw)':'-2'[s];"
            f"[1:v]scale='min({PREVIEW_W},iw)':'-2'[c];"
            f"[c][s]overlay=0:0,format=rgba"
        )
        _run([FFMPEG, "-hide_banner", "-loglevel", "error", "-y",
              "-i", src, "-i", ensure_checker(),
              "-frames:v", "1", "-filter_complex", vf, out])
    else:
        _run([FFMPEG, "-hide_banner", "-loglevel", "error", "-y",
              "-i", src, "-frames:v", "1",
              "-vf", f"scale='min({PREVIEW_W},iw)':'-2'", out])
    if not os.path.exists(out):
        raise MediaError("preview not produced")
    return out


def clear_cache_files(asset_id):
    for d in (THUMBS, PROXIES, PREVIEWS):
        for ext in ("jpg", "mp4", "png"):
            p = os.path.join(d, f"{asset_id}.{ext}")
            if os.path.exists(p):
                try:
                    os.remove(p)
                except OSError:
                    pass
