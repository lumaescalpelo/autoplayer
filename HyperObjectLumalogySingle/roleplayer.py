#!/usr/bin/env python3
# -------------------------------------------------------
# Raspberry Pi 4B+ — Sistema audiovisual robusto
# Basado en control temporal desde Python (SIN IPC)
# -------------------------------------------------------

import os
import random
import subprocess
import time
import threading
from pathlib import Path

# =======================
# CONFIG
# =======================

ROLE = 0                  # 0=leader, 1..3 followers
ORIENTATION = "hor"       # "hor" o "ver"

BASE_VIDEO_DIR = Path.home() / "Videos" / "videos_hd_final"
BASE_AUDIO_DIR = Path.home() / "Music" / "audios"

VIDEO_EXTENSIONS = (".mp4", ".mov", ".mkv")

# Duraciones (segundos)
BLOCK_DURATION = 40       # duración total por categoría
VIDEO_DURATION = 10       # duración por video
BLACK_GAP = 0.2           # negro entre bloques

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
                "--no-terminal", "--quiet",
                "--loop-file=inf",
                "--audio-display=no",
                str(pick_audio())
            ])
        time.sleep(1)

# =======================
# VIDEO
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

def pick_block(cat: str):
    text_dir, vid_dir = category_dirs(cat)

    textos = [p for p in text_dir.iterdir() if is_video(p)] if text_dir.exists() else []
    vids   = [p for p in vid_dir.iterdir() if is_video(p)] if vid_dir.exists() else []

    if not textos or len(vids) < 3:
        return []

    return [random.choice(textos)] + random.sample(vids, 3)

def play_video(path: Path, duration: float):
    subprocess.run([
        "mpv",
        "--fs",
        "--no-terminal", "--really-quiet",
        "--panscan=1.0",
        "--no-keepaspect-window",
        "--video-aspect-override=no",
        f"--length={duration}",
        str(path)
    ])

def play_black(duration: float):
    subprocess.run([
        "mpv",
        "--fs",
        "--no-terminal", "--really-quiet",
        "--vid=no",
        "--vo=gpu",
        f"--length={duration}"
    ])

def video_loop(stop_evt):
    categories = [d.name for d in BASE_VIDEO_DIR.iterdir() if d.is_dir()]

    while not stop_evt.is_set():
        cat = random.choice(categories)
        block = pick_block(cat)

        if not block:
            time.sleep(0.2)
            continue

        start = time.time()
        for video in block:
            elapsed = time.time() - start
            remaining = BLOCK_DURATION - elapsed
            if remaining <= 0:
                break

            play_video(video, min(VIDEO_DURATION, remaining))

        # negro entre bloques (oculta escritorio)
        play_black(BLACK_GAP)

# =======================
# MAIN
# =======================

def main():
    stop = threading.Event()

    threading.Thread(target=audio_loop, args=(stop,), daemon=True).start()
    threading.Thread(target=video_loop, args=(stop,), daemon=True).start()

    print(f"✅ Sistema corriendo | ROLE={ROLE} | ORIENTATION={ORIENTATION}")

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        stop.set()

if __name__ == "__main__":
    main()
