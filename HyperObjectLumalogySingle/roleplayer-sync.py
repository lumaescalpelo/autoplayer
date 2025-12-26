#!/usr/bin/env python3
# -------------------------------------------------------
# Raspberry Pi 4B+ — RolePlayer IPC (CLEAN / STABLE)
# Leader standalone + preparado para followers
# -------------------------------------------------------

import json
import os
import random
import socket
import subprocess
import threading
import time
from pathlib import Path

# =======================
# CONFIG
# =======================

ROLE = 0  # 0 = leader, 1..3 = followers (por ahora solo leader)
ORIENTATION = "hor"  # hor | ver | inverted_hor | inverted_ver

BASE_VIDEO_DIR = Path.home() / "Videos" / "videos_hd_final"
BASE_AUDIO_DIR = Path.home() / "Music" / "audios"

VIDEO_EXTENSIONS = (".mp4", ".mov", ".mkv")
BLOCK_SIZE = 4

IPC_SOCKET = f"/tmp/mpv_roleplayer_{ROLE}.sock"

# =======================
# ORIENTATION MAP
# =======================

ORIENTATION_MAP = {
    "hor": {"rotation": 0, "text_dir": "hor_text", "video_dir": "hor"},
    "ver": {"rotation": 0, "text_dir": "ver_rotated_text", "video_dir": "ver_rotated"},
    "inverted_hor": {"rotation": 180, "text_dir": "hor_text", "video_dir": "hor"},
    "inverted_ver": {"rotation": 180, "text_dir": "ver_rotated_text", "video_dir": "ver_rotated"},
}

# =======================
# LOG
# =======================

def log(msg):
    print(msg, flush=True)

# =======================
# FILES
# =======================

def is_video(p: Path):
    return p.is_file() and p.suffix.lower() in VIDEO_EXTENSIONS

def category_dirs(cat: str):
    cfg = ORIENTATION_MAP[ORIENTATION]
    return (
        BASE_VIDEO_DIR / cat / cfg["text_dir"],
        BASE_VIDEO_DIR / cat / cfg["video_dir"],
    )

def pick_block(cat: str):
    text_dir, vid_dir = category_dirs(cat)

    textos = [p for p in text_dir.iterdir() if is_video(p)] if text_dir.exists() else []
    vids   = [p for p in vid_dir.iterdir() if is_video(p)] if vid_dir.exists() else []

    if not textos or len(vids) < 3:
        return None

    return [random.choice(textos)] + random.sample(vids, 3)

def all_categories():
    return [d.name for d in BASE_VIDEO_DIR.iterdir() if d.is_dir()]

# =======================
# MPV IPC (CLEAN)
# =======================

class MPVIPC:
    def __init__(self, sock):
        self.sock = sock
        self.lock = threading.Lock()
        self.req_id = 0

    def _send(self, payload: dict):
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        s.connect(self.sock)
        s.sendall((json.dumps(payload) + "\n").encode())
        s.close()

    def cmd(self, command: list):
        """Comando con respuesta (solo para get/set simples)"""
        with self.lock:
            self.req_id += 1
            payload = {"command": command, "request_id": self.req_id}
            s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            s.connect(self.sock)
            s.sendall((json.dumps(payload) + "\n").encode())

            buf = b""
            while b"\n" not in buf:
                buf += s.recv(4096)
            s.close()

            try:
                return json.loads(buf.decode(errors="ignore"))
            except:
                return {}

    def cmd_noreply(self, command: list):
        """Fire-and-forget (loadfile, append-play, etc.)"""
        with self.lock:
            payload = {"command": command}
            self._send(payload)

# =======================
# MPV START
# =======================

def start_mpv():
    if os.path.exists(IPC_SOCKET):
        os.remove(IPC_SOCKET)

    rot = ORIENTATION_MAP[ORIENTATION]["rotation"]

    subprocess.Popen([
        "mpv",
        "--idle=yes",
        "--fs",
        "--force-window=yes",
        "--keep-open=yes",
        "--no-terminal",
        "--quiet",
        "--hwdec=drm-copy",
        "--vd=no",
        "--vo=gpu",
        "--scale=bilinear",
        f"--video-rotate={rot}",
        "--panscan=1.0",
        "--stop-screensaver=yes",
        f"--input-ipc-server={IPC_SOCKET}",
    ])

    for _ in range(100):
        if os.path.exists(IPC_SOCKET):
            break
        time.sleep(0.1)
    else:
        raise RuntimeError("mpv IPC no apareció")

    ipc = MPVIPC(IPC_SOCKET)

    # prueba real
    r = ipc.cmd(["get_property", "idle-active"])
    log(f"[MPV] IPC listo, idle-active={r.get('data')}")

    return ipc

# =======================
# MPV EVENTS
# =======================

endfile_count = 0
endfile_lock = threading.Lock()

def mpv_event_listener():
    global endfile_count
    while True:
        try:
            s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            s.connect(IPC_SOCKET)
            buf = b""
            while True:
                buf += s.recv(4096)
                while b"\n" in buf:
                    line, buf = buf.split(b"\n", 1)
                    try:
                        evt = json.loads(line.decode(errors="ignore"))
                    except:
                        continue
                    if evt.get("event") == "end-file":
                        with endfile_lock:
                            endfile_count += 1
        except:
            time.sleep(0.2)

# =======================
# LOAD BLOCK
# =======================

def mpv_load_block(ipc: MPVIPC, block):
    global endfile_count

    with endfile_lock:
        endfile_count = 0

    ipc.cmd(["set_property", "pause", True])

    ipc.cmd_noreply(["loadfile", str(block[0]), "replace"])
    for p in block[1:]:
        ipc.cmd_noreply(["loadfile", str(p), "append-play"])

    ipc.cmd(["set_property", "pause", False])

def wait_block():
    while True:
        with endfile_lock:
            if endfile_count >= BLOCK_SIZE:
                return
        time.sleep(0.05)

# =======================
# AUDIO
# =======================

def audio_loop():
    audio = BASE_AUDIO_DIR / "drone_81.WAV"
    while True:
        subprocess.run([
            "mpv",
            "--no-terminal",
            "--quiet",
            "--vd=no",
            "--loop-file=inf",
            "--audio-display=no",
            str(audio)
        ])
        time.sleep(1)

# =======================
# MAIN (LEADER SOLO)
# =======================

def main():
    threading.Thread(target=audio_loop, daemon=True).start()

    ipc = start_mpv()
    threading.Thread(target=mpv_event_listener, daemon=True).start()

    cats = all_categories()
    log(f"[LEADER] Categorías detectadas: {len(cats)}")

    while True:
        random.shuffle(cats)
        for cat in cats:
            block = pick_block(cat)
            if not block:
                continue

            log(f"[LEADER] ▶ {cat}")
            mpv_load_block(ipc, block)
            wait_block()

if __name__ == "__main__":
    main()
