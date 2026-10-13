"""Metadata extraction: ffprobe + native PNG/EXR header readers.

Alpha policy: has_alpha is 1 (yes), 0 (no) or None (unknown).
A failed probe must never be reported as "no alpha".
"""
import json
import os
import shutil
import struct
import subprocess

FFMPEG = shutil.which("ffmpeg") or "ffmpeg"
FFPROBE = shutil.which("ffprobe") or "ffprobe"

VIDEO_EXTS = {".mov", ".mp4", ".mxf", ".avi", ".mkv", ".webm", ".wmv", ".mpg", ".m2v", ".ts", ".mts"}
IMAGE_EXTS = {".png", ".tif", ".tiff", ".exr", ".jpg", ".jpeg", ".bmp"}


class MediaError(Exception):
    pass


def ffprobe(path: str) -> dict:
    try:
        r = subprocess.run(
            [FFPROBE, "-hide_banner", "-loglevel", "error",
             "-print_format", "json", "-show_format", "-show_streams", str(path)],
            capture_output=True, text=True, timeout=120)
    except subprocess.TimeoutExpired:
        raise MediaError("ffprobe timed out")
    if r.returncode != 0:
        raise MediaError((r.stderr or "ffprobe failed").strip()[:400])
    try:
        return json.loads(r.stdout)
    except json.JSONDecodeError:
        raise MediaError("ffprobe returned invalid JSON")


def _fmt_to_float(s):
    try:
        if "/" in s:
            a, b = s.split("/")
            b = float(b)
            return float(a) / b if b else None
        return float(s)
    except (ValueError, ZeroDivisionError):
        return None


def probe_video(path: str) -> dict:
    info = ffprobe(path)
    v = next((s for s in info.get("streams", []) if s.get("codec_type") == "video"), None)
    if v is None:
        raise MediaError("no video stream")
    pix = v.get("pix_fmt", "")
    fmt = info.get("format", {})
    dur = _fmt_to_float(fmt.get("duration", ""))
    return {
        "width": v.get("width"),
        "height": v.get("height"),
        "fps": _fmt_to_float(v.get("avg_frame_rate", "")) or _fmt_to_float(v.get("r_frame_rate", "")),
        "duration": dur,
        "bit_depth": v.get("bits_per_raw_sample"),
        "has_alpha": 1 if "a" in pix else 0,
        "channels": pix,
        "codec": v.get("codec_name"),
        "format": fmt.get("format_name", ""),
    }


def _read_png_header(path: str) -> dict:
    with open(path, "rb") as f:
        head = f.read(33)
    if len(head) < 33 or head[:8] != b"\x89PNG\r\n\x1a\n":
        raise MediaError("not a PNG")
    w, h, bit_depth, color_type = struct.unpack(">IIBB", head[16:26])
    alpha = {0: 0, 2: 0, 3: 1, 4: 1, 6: 1}.get(color_type)
    return {
        "width": w, "height": h, "bit_depth": bit_depth,
        "has_alpha": alpha,
        "channels": {0: "gray", 2: "RGB", 3: "palette", 4: "gray+A", 6: "RGBA"}.get(color_type, "unknown"),
        "codec": "png", "format": "png",
    }


def _read_exr_header(path: str) -> dict:
    with open(path, "rb") as f:
        magic = f.read(4)
        if magic != b"\x76\x2f\x31\x01":
            raise MediaError("not an OpenEXR file")
        f.read(4)  # version field

        def next_name():
            """Read next attribute name, skipping 0-7 padding bytes.
            '' = end of header, None = EOF."""
            for _ in range(8):
                ch = f.read(1)
                if not ch:
                    return None
                b = ch[0]
                if b == 0:
                    return ""
                if 32 <= b < 127:
                    name_b = ch
                    while True:
                        c = f.read(1)
                        if not c or c == b"\x00":
                            return name_b.decode("latin1")
                        name_b += c
            raise MediaError("corrupt EXR header (bad padding)")

        def next_str():
            b = b""
            while True:
                ch = f.read(1)
                if not ch:
                    raise MediaError("corrupt EXR header (truncated)")
                if ch == b"\x00":
                    return b.decode("latin1")
                b += ch

        channels = []
        w = h = None
        bit_depth = None
        while True:
            name = next_name()
            if name is None:
                raise MediaError("corrupt EXR header (truncated)")
            if name == "":
                break
            atype = next_str()  # e.g. "chlist", "box2i", "compression"
            sz_b = f.read(4)
            if len(sz_b) < 4:
                raise MediaError("corrupt EXR header (missing size)")
            n = struct.unpack("<i", sz_b)[0]
            if n < 0 or n > 10_000_000:
                raise MediaError("corrupt EXR header (implausible size)")
            val = f.read(n)
            if len(val) < n:
                raise MediaError("corrupt EXR header (truncated value)")
            if name == "channels":
                # channel list: (name\0, ptype:1, plinear:1, xs:4, ys:4)* + \0
                # writers may pad entries (12-byte spec stride, 18 observed)
                buf = bytes(val)
                i = 0
                while i < len(buf):
                    j = buf.find(b"\x00", i)
                    if j < 0:
                        break
                    cname = buf[i:j].decode("latin1")
                    if not cname:
                        break  # terminator
                    ptype = buf[j + 1] if j + 1 < len(buf) else None
                    if ptype not in (1, 2, 3):
                        i = j + 1  # spurious name, skip
                        continue
                    if ptype == 3:
                        bit_depth = 32
                    elif ptype == 2 and (bit_depth is None or bit_depth < 16):
                        bit_depth = 16
                    channels.append(cname)
                    nxt = None
                    for p in (j + 11, j + 17):
                        if p >= len(buf):
                            continue
                        b = buf[p]
                        if 32 <= b < 127:
                            nxt = p
                            break
                        if b == 0 and p + 1 >= len(buf):
                            nxt = p  # real terminator
                            break
                    i = nxt if nxt is not None else len(buf)
            elif name == "dataWindow" and len(val) >= 16:
                xmin, ymin, xmax, ymax = struct.unpack("<4i", val[:16])
                w, h = xmax - xmin + 1, ymax - ymin + 1
        if not channels:
            raise MediaError("EXR has no channel list")
        alpha = 1 if ("A" in channels) else 0
        return {
            "width": w, "height": h, "bit_depth": bit_depth,
            "has_alpha": alpha,
            "channels": ",".join(channels),
            "codec": "exr", "format": "exr",
        }


def probe_image(path: str) -> dict:
    ext = os.path.splitext(path)[1].lower()
    if ext == ".png":
        return _read_png_header(path)
    if ext == ".exr":
        return _read_exr_header(path)
    # tif/jpg/bmp via ffprobe
    p = ffprobe(path)
    v = next((s for s in p.get("streams", []) if s.get("codec_type") == "video"), None)
    if v is None:
        raise MediaError("no image stream")
    pix = v.get("pix_fmt", "")
    alpha = None
    if ext in (".tif", ".tiff"):
        # samples=4 with photometric 5/8 => alpha; ffprobe rarely exposes this reliably
        if "a" in pix:
            alpha = 1
        elif v.get("sample_count") == 4:
            alpha = 1
    elif "a" in pix:
        alpha = 1
    else:
        alpha = 0
    return {
        "width": v.get("width"), "height": v.get("height"),
        "fps": None, "duration": 0.0,
        "bit_depth": v.get("bits_per_raw_sample"),
        "has_alpha": alpha,
        "channels": pix,
        "codec": v.get("codec_name"),
        "format": v.get("codec_name", ext.lstrip(".")),
    }


def probe(path: str, kind: str) -> dict:
    if kind == "video":
        return probe_video(path)
    return probe_image(path)
