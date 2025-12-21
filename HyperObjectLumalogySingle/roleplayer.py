#!/usr/bin/env python3
# -------------------------------------------------------
# Raspberry Pi 4B+ — Sistema audiovisual Leader / Followers
# - Audio infinito por rol (watchdog)
# - Video en una sola instancia mpv (IPC)
# - Playlist larga, regenerativa y observable
# - Compatible con hor / hor_text / ver_rotated / ver_rotated_text
# -------------------------------------------------------

import os
import time
import json
import random
import subprocess
import threading
from pathlib import Path
import socket

# =======================
# CONFIG PRINCIPAL
# =======================

ROLE = 0                  # 0=leader, 1,2,3=followers
ORIENTATION = "hor"       # "hor" o "ver"

ROUNDS = 10
BLOCK_TEXT_COUNT = 1
BLOCK_NO_TEXT_COUNT = 3

BASE_VIDEO_DIR = Path.home() / "Videos" / "videos_hd_final"
BASE_AUDIO_DIR = Path.home() / "Music" / "audios"

VIDEO_EXTENSIONS = (".mp4", ".mov", ".mkv")

IPC_DIR = Path("/tmp")
VIDEO_IPC = IPC_DIR / f"mpv_video_role{ROLE}.sock"

DEBUG_PLAYLIST_PATH = Path.home() / f"video_system_last_playlist_role{ROLE}.m3u"
TMP_PLAYLIST_PATH   = Path("/tmp") / f"playlist_role{ROLE}.m3u"

# =======================
# UTILIDADES
# =======================

def is_video_file(p: Path) -> bool:
    return p.is_file() and p.suffix.lower() in VIDEO_EXTENSIONS

def pick_audio_by_role(role: int) -> Path:
    mapping = {
        0: "drone_81.WAV",
        1: "drone_82.WAV",
        2: "drone_83.WAV",
        3: "drone_84.WAV",
    }
    if role not in mapping:
        raise ValueError("ROLE inválido (usa 0..3)")
    return BASE_AUDIO_DIR / mapping[role]

# =======================
# CATEGORÍAS
# =======================

def list_categories(base_dir: Path) -> list[str]:
    return sorted([d.name for d in base_dir.iterdir() if d.is_dir()])

def category_dirs(cat: str, orientation: str) -> tuple[Path, Path]:
    if orientation == "hor":
        text_dir = BASE_VIDEO_DIR / cat / "hor_text"
        vid_dir  = BASE_VIDEO_DIR / cat / "hor"
    elif orientation == "ver":
        text_dir = BASE_VIDEO_DIR / cat / "ver_rotated_text"
        vid_dir  = BASE_VIDEO_DIR / cat / "ver_rotated"
    else:
        raise ValueError("ORIENTATION debe ser 'hor' o 'ver'")
    return text_dir, vid_dir

def pick_block_for_category(cat: str, orientation: str) -> list[Path]:
    text_dir, vid_dir = category_dirs(cat, orientation)

    text_videos = []
    video_videos = []

    if text_dir.is_dir():
        text_videos = [p for p in text_dir.iterdir() if is_video_file(p)]
    if vid_dir.is_dir():
        video_videos = [p for p in vid_dir.iterdir() if is_video_file(p)]

    if len(text_videos) < BLOCK_TEXT_COUNT:
        return []
    if len(video_videos) < BLOCK_NO_TEXT_COUNT:
        return []

    block = []
    block.extend(random.sample(text_videos, BLOCK_TEXT_COUNT))
    block.extend(random.sample(video_videos, BLOCK_NO_TEXT_COUNT))
    return block

def build_long_playlist(orientation: str) -> list[tuple[str, list[Path]]]:
    cats = list_categories(BASE_VIDEO_DIR)
    result: list[tuple[str, list[Path]]] = []

    for _ in range(ROUNDS):
        shuffled = cats[:]
        random.shuffle(shuffled)
        for cat in shuffled:
            block = pick_block_for_category(cat, orientation)
            if block:
                result.append((cat, block))
            else:
                print(f"⚠️  Categoría omitida (faltan videos): {cat}")

    return result

def write_m3u(blocks: list[tuple[str, list[Path]]], out_path: Path):
    with out_path.open("w", encoding="utf-8") as f:
        for cat, paths in blocks:
            f.write(f"# === {cat} ===\n")
            for p in paths:
                f.write(str(p) + "\n")

# =======================
# MPV IPC
# =======================

class MPVIPC:
    def __init__(self, sock_path: Path):
        self.sock_path = sock_path

    def _send(self, obj: dict):
        msg = (json.dumps(obj) + "\n").encode("utf-8")
        try:
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
                s.settimeout(1.0)
                s.connect(str(self.sock_path))
                s.sendall(msg)
                data = s.recv(65536)
            if not data:
                return None
            for line in data.splitlines():
                try:
                    return json.loads(line.decode())
                except Exception:
                    pass
        except Exception:
            return None

    def get_property(self, name: str):
        r = self._send({"command": ["get_property", name]})
        if r and r.get("error") == "success":
            return r.get("data")
        return None

    def loadlist_replace(self, path: Path):
        return self._send({"command": ["loadlist", str(path), "replace"]})

# =======================
# MPV PROCESOS
# =======================

def launch_audio_mpv(audio_path: Path) -> subprocess.Popen:
    return subprocess.Popen([
        "mpv",
        "--no-terminal", "--quiet",
        "--loop-file=inf",
        "--audio-display=no",
        str(audio_path),
    ])

def launch_video_mpv(ipc_path: Path) -> subprocess.Popen:
    if ipc_path.exists():
        try:
            ipc_path.unlink()
        except Exception:
            pass

    return subprocess.Popen([
        "mpv",
        "--no-terminal", "--quiet",
        "--fs",
        "--force-window=yes",
        "--idle=yes",
        "--keep-open=yes",
        "--input-ipc-server=" + str(ipc_path),

        "--hwdec=auto-safe",
        "--vo=gpu",
        "--profile=fast",
        "--cache=yes",
        "--cache-secs=10",
        "--vd-lavc-threads=2",
        "--framedrop=vo",
        "--video-sync=display-resample",
        "--interpolation=no",
        "--stop-screensaver=yes",
    ])

# =======================
# WATCHDOG AUDIO
# =======================

def audio_watchdog(stop_evt: threading.Event):
    audio = pick_audio_by_role(ROLE)
    proc = None

    while not stop_evt.is_set():
        if proc is None or proc.poll() is not None:
            print("🔊 Lanzando audio:", audio)
            proc = launch_audio_mpv(audio)
        time.sleep(1)

# =======================
# VIDEO + PLAYLIST MANAGER
# =======================

def video_manager(stop_evt: threading.Event):
    proc = launch_video_mpv(VIDEO_IPC)

    # esperar IPC
    for _ in range(50):
        if VIDEO_IPC.exists():
            break
        time.sleep(0.2)

    ipc = MPVIPC(VIDEO_IPC)

    def rebuild_and_load():
        blocks = build_long_playlist(ORIENTATION)
        if not blocks:
            print("❌ Playlist vacía")
            return False

        write_m3u(blocks, DEBUG_PLAYLIST_PATH)
        write_m3u(blocks, TMP_PLAYLIST_PATH)

        # garantizar que el archivo esté escrito
        if not TMP_PLAYLIST_PATH.exists() or TMP_PLAYLIST_PATH.stat().st_size == 0:
            print("❌ Playlist no escrita correctamente")
            return False

        print(f"✔ Playlist lista ({len(blocks)} bloques)")
        time.sleep(0.2)

        r = ipc.loadlist_replace(TMP_PLAYLIST_PATH)
        if not r or r.get("error") != "success":
            print("❌ mpv rechazó loadlist")
            return False

        return True

    # primera carga (espera hasta que funcione)
    while not rebuild_and_load():
        print("⏳ Reintentando carga inicial…")
        time.sleep(1)

    idle_since = None

    while not stop_evt.is_set():
        if proc.poll() is not None:
            print("🎬 mpv murió, relanzando")
            proc = launch_video_mpv(VIDEO_IPC)
            time.sleep(1)
            ipc = MPVIPC(VIDEO_IPC)
            rebuild_and_load()
            idle_since = None
            continue

        idle = ipc.get_property("idle-active")
        if idle:
            if idle_since is None:
                idle_since = time.time()
            elif time.time() - idle_since > 1.0:
                print("🔁 Playlist terminada, regenerando")
                rebuild_and_load()
                idle_since = None
        else:
            idle_since = None

        time.sleep(0.5)

# =======================
# MAIN
# =======================

def main():
    if not BASE_VIDEO_DIR.is_dir():
        raise RuntimeError("BASE_VIDEO_DIR no existe")
    if not BASE_AUDIO_DIR.is_dir():
        raise RuntimeError("BASE_AUDIO_DIR no existe")

    stop_evt = threading.Event()

    threading.Thread(target=audio_watchdog, args=(stop_evt,), daemon=True).start()
    threading.Thread(target=video_manager, args=(stop_evt,), daemon=True).start()

    print(f"✅ Sistema corriendo | ROLE={ROLE} | ORIENTATION={ORIENTATION}")

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        stop_evt.set()
        time.sleep(1)

if __name__ == "__main__":
    main()
