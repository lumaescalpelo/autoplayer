#!/usr/bin/env python3
# -------------------------------------------------------
# Raspberry Pi 4B+ — Autoplayer por playlist (FINAL)
# -------------------------------------------------------

import random
import subprocess
import time
import threading
from pathlib import Path

# =======================
# CONFIG
# =======================

ROLE = 0                  # 0 = leader, 1..3 followers
ORIENTATION = "hor"       # "hor" | "ver"

ROUNDS = 10               # cuántas veces repetir todas las categorías

BASE_VIDEO_DIR = Path.home() / "Videos" / "videos_hd_final"
BASE_AUDIO_DIR = Path.home() / "Music" / "audios"

VIDEO_EXTENSIONS = (".mp4", ".mov", ".mkv")

PLAYLIST_PATH = Path("/tmp") / f"playlist_role{ROLE}.m3u"

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
            print("🔊 Lanzando audio")
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
    if ORIENTATION == "hor":
        return (
            BASE_VIDEO_DIR / cat / "hor_text",
            BASE_VIDEO_DIR / cat / "hor",
        )
    else:
        return (
            BASE_VIDEO_DIR / cat / "ver_rotated_text",
            BASE_VIDEO_DIR / cat / "ver_rotated",
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
            for v in block:
                lines.append(str(v))

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
    while not stop_evt.is_set():
        if not write_playlist():
            time.sleep(1)
            continue

        print("🎬 Lanzando mpv con playlist")
        proc = subprocess.Popen([
            "mpv",

            "--fs",
            "--force-window=yes",
            "--keep-open=yes",

            f"--playlist={PLAYLIST_PATH}",
            "--loop-playlist=no",

            # Flags válidos y estables en Raspberry Pi
            "--hwdec=auto-safe",
            "--vo=gpu",
            "--scale=bilinear",

            # 🔥 FILL TOTAL (sin barras negras)
            "--video-zoom=0.999",
            "--video-pan=0:0",

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
