#!/usr/bin/env python3
# -------------------------------------------------------
# Raspberry Pi 4B+ — Autoplayer mpv (rotación de pantalla)
# -------------------------------------------------------

import random
import subprocess
import time
import threading
from pathlib import Path

# =======================
# CONFIG
# =======================

ROLE = 0   # 0 = leader, 1..3 followers

# Orientación física de la pantalla
# hor | ver | inverted_hor | inverted_ver
ORIENTATION = "hor"

ROUNDS = 10

BASE_VIDEO_DIR = Path.home() / "Videos" / "videos_hd_final"
BASE_AUDIO_DIR = Path.home() / "Music" / "audios"

VIDEO_EXTENSIONS = (".mp4", ".mov", ".mkv")
PLAYLIST_PATH = Path("/tmp") / f"playlist_role{ROLE}.m3u"

# =======================
# ORIENTATION MAP
# =======================

ORIENTATION_MAP = {
    "hor": {
        "rotation": 0,
        "text_dir": "hor_text",
        "video_dir": "hor",
    },
    "ver": {
        "rotation": 90,
        "text_dir": "ver_rotated_text",
        "video_dir": "ver_rotated",
    },
    "inverted_hor": {
        "rotation": 180,
        "text_dir": "hor_text",
        "video_dir": "hor",
    },
    "inverted_ver": {
        "rotation": 270,
        "text_dir": "ver_rotated_text",
        "video_dir": "ver_rotated",
    },
}

# =======================
# AUDIO
# =======================

def pick_audio():
    return BASE_AUDIO_DIR / {
        0: "drone_81.WAV",
        1: "drone_82.WAV",
        2: "drone_83.WAV",
        3: "drone_84.WAV",
    }[ROLE]

def audio_loop(stop_evt):
    proc = None
    while not stop_evt.is_set():
        if proc is None or proc.poll() is not None:
            proc = subprocess.Popen([
                "mpv",
                "--no-terminal",
                "--loop-file=inf",
                "--audio-display=no",
                str(pick_audio())
            ])
        time.sleep(1)

# =======================
# VIDEO PLAYLIST
# =======================

def is_video(p: Path):
    return p.is_file() and p.suffix.lower() in VIDEO_EXTENSIONS

def category_dirs(cat: str):
    cfg = ORIENTATION_MAP[ORIENTATION]
    return (
        BASE_VIDEO_DIR / cat / cfg["text_dir"],
        BASE_VIDEO_DIR / cat / cfg["video_dir"],
    )

def build_playlist():
    categories = [d.name for d in BASE_VIDEO_DIR.iterdir() if d.is_dir()]
    lines = []

    for _ in range(ROUNDS):
        random.shuffle(categories)
        for cat in categories:
            text_dir, vid_dir = category_dirs(cat)

            textos = [p for p in text_dir.iterdir() if is_video(p)] if text_dir.exists() else []
            vids   = [p for p in vid_dir.iterdir() if is_video(p)] if vid_dir.exists() else []

            if not textos or len(vids) < 3:
                continue

            block = [random.choice(textos)] + random.sample(vids, 3)
            lines.extend(str(v) for v in block)

    return lines

def write_playlist():
    lines = build_playlist()
    if not lines:
        print("❌ Playlist vacía")
        return False

    with PLAYLIST_PATH.open("w", encoding="utf-8") as f:
        for line in lines:
            f.write(line + "\n")

    print(f"📼 Playlist creada: {len(lines)} videos")
    return True

# =======================
# VIDEO LOOP
# =======================

def video_loop(stop_evt):
    rotation = ORIENTATION_MAP[ORIENTATION]["rotation"]

    while not stop_evt.is_set():
        if not write_playlist():
            time.sleep(1)
            continue

        print(f"🎬 Lanzando mpv | rotación={rotation}°")

        proc = subprocess.Popen([
            "mpv",

            "--fs",
            "--force-window=yes",
            "--keep-open=yes",

            f"--playlist={PLAYLIST_PATH}",
            "--loop-playlist=no",

            "--hwdec=auto-safe",
            "--vo=gpu",
            "--scale=bilinear",

            # Rotación física de pantalla
            f"--video-rotate={rotation}",

            # Fill suave (el que decidiste mantener)
            "--panscan=1.0",
            "--no-keepaspect-window",
            "--video-aspect-override=no",

            "--stop-screensaver=yes",
        ])

        proc.wait()
        print("🔁 Playlist terminada, regenerando")

# =======================
# MAIN
# =======================

def main():
    stop = threading.Event()

    threading.Thread(target=audio_loop, args=(stop,), daemon=True).start()
    threading.Thread(target=video_loop, args=(stop,), daemon=True).start()

    print(f"✅ Autoplayer activo | ROLE={ROLE} | ORIENTATION={ORIENTATION}")

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        stop.set()

if __name__ == "__main__":
    main()
