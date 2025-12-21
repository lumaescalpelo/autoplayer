#!/usr/bin/env python3
# -------------------------------------------------------
# Raspberry Pi 4B+ — Sistema audiovisual Leader / Followers
# SOLUCIÓN FINAL: mpv + playlist-watch (SIN IPC)
# -------------------------------------------------------

import time
import random
import subprocess
import threading
from pathlib import Path

# =======================
# CONFIGURACIÓN
# =======================

ROLE = 0                  # 0 = leader, 1 / 2 / 3 = followers
ORIENTATION = "hor"       # "hor" o "ver"

ROUNDS = 10
BLOCK_TEXT_COUNT = 1
BLOCK_NO_TEXT_COUNT = 3

BASE_VIDEO_DIR = Path.home() / "Videos" / "videos_hd_final"
BASE_AUDIO_DIR = Path.home() / "Music" / "audios"

VIDEO_EXTENSIONS = (".mp4", ".mov", ".mkv")

PLAYLIST_PATH = Path("/tmp") / f"playlist_role{ROLE}.m3u"
DEBUG_PLAYLIST = Path.home() / f"video_system_last_playlist_role{ROLE}.m3u"

# =======================
# UTILIDADES
# =======================

def is_video(p: Path) -> bool:
    return p.is_file() and p.suffix.lower() in VIDEO_EXTENSIONS

def pick_audio(role: int) -> Path:
    return BASE_AUDIO_DIR / {
        0: "drone_81.WAV",
        1: "drone_82.WAV",
        2: "drone_83.WAV",
        3: "drone_84.WAV",
    }[role]

# =======================
# CATEGORÍAS
# =======================

def list_categories():
    return sorted(d.name for d in BASE_VIDEO_DIR.iterdir() if d.is_dir())

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

    text_v = [p for p in text_dir.iterdir() if is_video(p)] if text_dir.is_dir() else []
    vid_v  = [p for p in vid_dir.iterdir() if is_video(p)] if vid_dir.is_dir() else []

    if len(text_v) < BLOCK_TEXT_COUNT or len(vid_v) < BLOCK_NO_TEXT_COUNT:
        return []

    return random.sample(text_v, 1) + random.sample(vid_v, 3)

def build_playlist():
    blocks = []
    cats = list_categories()

    for _ in range(ROUNDS):
        random.shuffle(cats)
        for cat in cats:
            block = pick_block(cat)
            if block:
                blocks.append((cat, block))
            else:
                print(f"⚠️  Categoría omitida: {cat}")

    return blocks

def write_m3u(blocks, path):
    with path.open("w", encoding="utf-8") as f:
        for cat, vids in blocks:
            f.write(f"# === {cat} ===\n")
            for v in vids:
                f.write(str(v) + "\n")

# =======================
# MPV PROCESOS
# =======================

def launch_audio():
    return subprocess.Popen([
        "mpv",
        "--no-terminal", "--quiet",
        "--loop-file=inf",
        "--audio-display=no",
        str(pick_audio(ROLE)),
    ])

def launch_video():
    return subprocess.Popen([
        "mpv",
        "--no-terminal", "--quiet",
        "--fs",
        "--force-window=yes",

        # Playlist dinámica
        f"--playlist={PLAYLIST_PATH}",
        "--playlist-watch=yes",
        "--loop-playlist=no",

        # Rendimiento Pi
        "--hwdec=auto-safe",
        "--vo=gpu",
        "--profile=fast",

        # Fullscreen SIN barras
        "--panscan=1.0",
        "--no-keepaspect-window",
        "--video-aspect-override=no",

        "--stop-screensaver=yes",
    ])

# =======================
# WATCHDOGS
# =======================

def audio_loop(stop):
    proc = None
    while not stop.is_set():
        if proc is None or proc.poll() is not None:
            print("🔊 Audio iniciado")
            proc = launch_audio()
        time.sleep(1)

def video_loop(stop):
    proc = None

    def regenerate_playlist():
        blocks = build_playlist()
        if not blocks:
            print("❌ Playlist vacía")
            return False

        write_m3u(blocks, PLAYLIST_PATH)
        write_m3u(blocks, DEBUG_PLAYLIST)

        print(f"✔ Playlist escrita ({len(blocks)} bloques)")
        return True

    # generar playlist inicial
    while not regenerate_playlist():
        time.sleep(1)

    proc = launch_video()
    print("🎬 mpv video lanzado")

    # loop principal
    last_size = PLAYLIST_PATH.stat().st_size

    while not stop.is_set():
        if proc.poll() is not None:
            print("🎬 mpv murió, relanzando")
            proc = launch_video()
            time.sleep(1)

        # detectar fin de playlist por tamaño estable y mpv sin archivos
        time.sleep(2)

        # regenerar periódicamente cuando mpv termine
        if proc.poll() is None:
            regenerate_playlist()

        time.sleep(5)

# =======================
# MAIN
# =======================

def main():
    stop = threading.Event()

    threading.Thread(target=audio_loop, args=(stop,), daemon=True).start()
    threading.Thread(target=video_loop, args=(stop,), daemon=True).start()

    print(f"✅ Sistema activo | ROLE={ROLE} | ORIENTATION={ORIENTATION}")

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        stop.set()

if __name__ == "__main__":
    main()
