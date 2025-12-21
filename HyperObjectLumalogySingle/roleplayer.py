#!/usr/bin/env python3
# -------------------------------------------------------
# Raspberry Pi 4B+ — Sistema audiovisual Leader / Followers
#
# Video:
#   - mpv único, fullscreen real, sin barras (panscan)
#   - Playlist regenerativa vía IPC (sin usar idle-active)
#
# Audio:
#   - Loop infinito por rol (watchdog)
#
# Compatible con:
#   hor / hor_text
#   ver_rotated / ver_rotated_text
# -------------------------------------------------------

import time
import json
import random
import subprocess
import threading
from pathlib import Path
import socket

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

VIDEO_IPC = Path("/tmp") / f"mpv_video_role{ROLE}.sock"
TMP_PLAYLIST = Path("/tmp") / f"playlist_role{ROLE}.m3u"
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
    result = []
    cats = list_categories()

    for _ in range(ROUNDS):
        random.shuffle(cats)
        for cat in cats:
            block = pick_block(cat)
            if block:
                result.append((cat, block))
            else:
                print(f"⚠️  Categoría omitida: {cat}")

    return result

def write_m3u(blocks, path):
    with path.open("w", encoding="utf-8") as f:
        for cat, vids in blocks:
            f.write(f"# === {cat} ===\n")
            for v in vids:
                f.write(str(v) + "\n")

# =======================
# MPV IPC
# =======================

class MPVIPC:
    def __init__(self, sock):
        self.sock = sock

    def cmd(self, payload):
        try:
            with socket.socket(socket.AF_UNIX) as s:
                s.settimeout(1)
                s.connect(str(self.sock))
                s.sendall((json.dumps(payload) + "\n").encode())
                data = s.recv(4096)
            for line in data.splitlines():
                try:
                    return json.loads(line)
                except Exception:
                    pass
        except Exception:
            return None

    def ready(self):
        r = self.cmd({"command": ["get_property", "pid"]})
        return r and r.get("error") == "success"

    def loadlist(self, path):
        return self.cmd({"command": ["loadlist", str(path), "replace"]})

def wait_for_ipc_ready(ipc, timeout=15):
    t0 = time.time()
    while time.time() - t0 < timeout:
        if ipc.ready():
            return True
        time.sleep(0.3)
    return False

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
    if VIDEO_IPC.exists():
        VIDEO_IPC.unlink()

    return subprocess.Popen([
        "mpv",
        "--no-terminal", "--quiet",
        "--fs",
        "--force-window=yes",
        "--idle=yes",
        "--keep-open=yes",
        "--input-ipc-server=" + str(VIDEO_IPC),

        # Rendimiento Raspberry Pi
        "--hwdec=auto-safe",
        "--vo=gpu",
        "--profile=fast",

        # Pantalla completa SIN barras
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
    proc = launch_video()

    # Esperar socket IPC
    for _ in range(60):
        if VIDEO_IPC.exists():
            break
        time.sleep(0.2)

    ipc = MPVIPC(VIDEO_IPC)

    def rebuild():
        blocks = build_playlist()
        if not blocks:
            print("❌ Playlist vacía")
            return False

        write_m3u(blocks, TMP_PLAYLIST)
        write_m3u(blocks, DEBUG_PLAYLIST)

        if not wait_for_ipc_ready(ipc):
            print("❌ mpv IPC no respondió")
            return False

        r = ipc.loadlist(TMP_PLAYLIST)
        if not r or r.get("error") != "success":
            print("❌ mpv rechazó loadlist", r)
            return False

        print(f"✔ Playlist cargada ({len(blocks)} bloques)")
        return True

    while not rebuild():
        time.sleep(1)

    idle_since = None

    while not stop.is_set():
        if proc.poll() is not None:
            print("🎬 mpv murió, relanzando")
            proc = launch_video()
            time.sleep(1)
            ipc = MPVIPC(VIDEO_IPC)
            rebuild()
            idle_since = None

        # Detectar fin de playlist: mpv vuelve a no tener archivo
        playing = ipc.cmd({"command": ["get_property", "filename"]})
        if not playing or playing.get("data") is None:
            if idle_since is None:
                idle_since = time.time()
            elif time.time() - idle_since > 1.0:
                print("🔁 Playlist terminada, regenerando")
                rebuild()
                idle_since = None
        else:
            idle_since = None

        time.sleep(0.5)

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
