#!/usr/bin/env python3
# -------------------------------------------------------
# Raspberry Pi 4B+ — Autoplayer por playlist (FINAL Bookworm)
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

def pick_audio() -> Path:
    return BASE_AUDIO_DIR / {
        0: "drone_81.WAV",
        1: "drone_82.WAV",
        2: "drone_83.WAV",
        3: "drone_84.WAV",
    }[ROLE]

def audio_loop(stop_evt: threading.Event) -> None:
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

def is_video(p: Path) -> bool:
    return p.is_file() and p.suffix.lower() in VIDEO_EXTENSIONS

def category_dirs(cat: str):
    # Nombres reales que me diste
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
            lines.extend(str(v) for v in block)

    return lines

def write_playlist() -> bool:
    lines = build_playlist()
    if not lines:
        print("❌ Playlist vacía (no hay categorías con >=1 texto y >=3 videos)")
        return False

    with PLAYLIST_PATH.open("w", encoding="utf-8") as f:
        for line in lines:
            f.write(line + "\n")

    print(f"📼 Playlist creada: {len(lines)} videos -> {PLAYLIST_PATH}")
    return True

# =======================
# VIDEO LOOP
# =======================

def video_loop(stop_evt: threading.Event) -> None:
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

            # Rendimiento Pi (Bookworm-friendly)
            "--hwdec=auto-safe",
            "--vo=gpu",
            "--scale=bilinear",

            # FILL total sin barras:
            # Esto recorta (crop) para llenar pantalla.
            "--video-zoom=0.999",

            # Si quieres dejarlo explícito (opcional):
            "--video-pan-x=0",
            "--video-pan-y=0",

            "--stop-screensaver=yes",
        ])

        proc.wait()
        print("🔁 Playlist terminada, regenerando")

# =======================
# MAIN
# =======================

def main() -> None:
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
