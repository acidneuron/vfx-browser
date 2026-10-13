"""Generate synthetic VFX test assets for pipeline verification."""
import os
import struct
import subprocess
import zlib

LIB = os.path.join(os.path.dirname(os.path.abspath(__file__)), "test_library")
FF = "ffmpeg"


def w_png(path, w, h, rows_rgba):
    def chunk(t, d):
        c = t + d
        return struct.pack(">I", len(d)) + c + struct.pack(">I", zlib.crc32(c) & 0xFFFFFFFF)
    raw = b"".join(b"\x00" + r for r in rows_rgba)
    png = b"\x89PNG\r\n\x1a\n"
    png += chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 6, 0, 0, 0))
    png += chunk(b"IDAT", zlib.compress(raw, 6))
    png += chunk(b"IEND", b"")
    with open(path, "wb") as f:
        f.write(png)


def run(*cmd):
    r = subprocess.run(list(cmd), capture_output=True, text=True)
    if r.returncode != 0:
        print("FAIL:", " ".join(cmd), "\n", r.stderr[-400:])
        raise SystemExit(1)


os.makedirs(os.path.join(LIB, "Explosions"), exist_ok=True)
os.makedirs(os.path.join(LIB, "Smoke"), exist_ok=True)
os.makedirs(os.path.join(LIB, "Lens Flares"), exist_ok=True)
os.makedirs(os.path.join(LIB, "Stills"), exist_ok=True)
os.makedirs(os.path.join(LIB, "Broken"), exist_ok=True)

# 1) EXR sequence with alpha (30 frames, 320x180)
run(FF, "-hide_banner", "-loglevel", "error", "-y",
    "-f", "lavfi", "-i", "testsrc2=s=320x180:r=25",
    "-frames:v", "30", "-pix_fmt", "rgbaf32",
    os.path.join(LIB, "Explosions", "explosion_alpha.%04d.exr"))

# 2) Video .mov (5s)
run(FF, "-hide_banner", "-loglevel", "error", "-y",
    "-f", "lavfi", "-i", "testsrc2=s=480x270:r=25",
    "-t", "5", "-c:v", "libx264", "-pix_fmt", "yuv420p",
    os.path.join(LIB, "Explosions", "explosion_burst.mov"))

# 3) Video .mp4 with hue drift
run(FF, "-hide_banner", "-loglevel", "error", "-y",
    "-f", "lavfi", "-i", "gradients=s=480x270:r=25:speed=0.1",
    "-t", "5", "-c:v", "libx264", "-pix_fmt", "yuv420p",
    os.path.join(LIB, "Smoke", "smoke_drift.mp4"))

# 4) RGBA PNG sequence, moving blob with transparency (30 frames)
for i in range(30):
    w = h = 320
    cx = 60 + i * 7
    rows = []
    for y in range(h):
        row = bytearray()
        for x in range(w):
            d2 = (x - cx) ** 2 + (y - h // 2) ** 2
            r = 70 - i
            if d2 < r * r:
                a = int(255 * (1 - (d2 ** 0.5) / r))
                row += bytes((30 + i * 3, 60 + i, 160, a))
            else:
                row += bytes((0, 0, 0, 0))
        rows.append(bytes(row))
    w_png(os.path.join(LIB, "Smoke", f"smoke_plume_alpha.{i + 1001:04d}.png"), w, h, rows)

# 5) Single RGBA star flare
w = h = 480
rows = []
for y in range(h):
    row = bytearray()
    for x in range(w):
        dx, dy = x - w // 2, y - h // 2
        d = (dx * dx + dy * dy) ** 0.5
        star = abs(dx) + abs(dy)
        a = 0
        if d < 140:
            a = int(220 * (1 - d / 140))
        if (abs(dx * 3) < 12 or abs(dy * 3) < 12) and d < 220:
            a = max(a, int(255 * (1 - d / 220)))
        row += bytes((255, 240, 200, a))
    rows.append(bytes(row))
w_png(os.path.join(LIB, "Lens Flares", "flare_01.png"), w, h, rows)

# 6) Plain JPG still
run(FF, "-hide_banner", "-loglevel", "error", "-y",
    "-f", "lavfi", "-i", "testsrc=s=640x360",
    "-frames:v", "1", "-q:v", "4",
    os.path.join(LIB, "Stills", "still_01.jpg"))

# 7) Broken file (garbage bytes with a valid extension)
with open(os.path.join(LIB, "Broken", "corrupt_shot.mp4"), "wb") as f:
    f.write(b"this is not a video file" * 10)

print("test library written to", LIB)
