#!/usr/bin/env python3
# -------------------------------------------------------
# Raspberry Pi 4B+ — RolePlayer IPC (leader standalone FIX)
# -------------------------------------------------------

import json
import os
import random
import socket
import subprocess
import threading
import time
from pathlib import Path

ROLE = 0
ORIENTATION = "hor"

BASE_VIDEO_DIR = Path.home() / "Videos" / "videos_hd_final"
BASE_AUDIO_DIR = Path.home() / "Music" / "audios"
VIDEO_EXTENSIONS = (".mp4", ".mov", ".mkv")

IPC_SOCKET = f"/tmp/mpv_roleplayer_{ROLE}.sock"
BLOCK_SIZE = 4

ORIENTATION_MAP = {
    "hor": {"rotation": 0, "text_dir": "hor_text", "video_dir": "hor"},
    "ver": {"rotation": 0, "text_dir": "ver_rotated_text", "video_dir": "ver_rotated"},
    "inverted_hor": {"rotation": 180, "text_dir": "hor_text", "video_dir": "hor"},
    "inverted_ver": {"rotation": 180, "text_dir": "ver_rotated_text", "video_dir": "ver_rotated"},
}

endfile_count = 0
endfile_lock = threading.Lock()

def log(msg):
    print(msg, flush=True)

# =======================
# FILES
# =======================

def is_video(p: Path):
    return p.is_file() and p.suffix.lower() in VIDEO_EXTENSIONS

def category_dirs(cat):
    cfg = ORIENTATION_MAP[ORIENTATION]
    return (
        BASE_VIDEO_DIR / cat / cfg["text_dir"],
        BASE_VIDEO_DIR / cat / cfg["video_dir"],
    )

def pick_block(cat):
    text_dir, vid_dir = category_dirs(cat)
    textos = list(text_dir.glob("*")) if text_dir.exists() else []
    vids   = list(vid_dir.glob("*")) if vid_dir.exists() else []

    textos = [p for p in textos if is_video(p)]
    vids   = [p for p in vids if is_video(p)]

    if not textos or len(vids) < 3:
        return None

    return [random.choice(textos)] + random.sample(vids, 3)

def all_categories():
    return [d.name for d in BASE_VIDEO_DIR.iterdir() if d.is_dir()]

# =======================
# MPV IPC
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
        "--no-terminal",
        "--quiet",
        "--keep-open=yes",
        "--hwdec=auto-safe",
        "--vo=gpu",
        "--scale=bilinear",
        f"--video-rotate={rot}",
        "--panscan=1.0",
        "--stop-screensaver=yes",
        f"--input-ipc-server={IPC_SOCKET}",
    ])

    for _ in range(100):
        if os.path.exists(IPC_SOCKET):
            return
        time.sleep(0.1)

    raise RuntimeError("mpv IPC no apareció")

def mpv_cmd(cmd):
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
        s.connect(IPC_SOCKET)
        s.sendall((json.dumps(cmd) + "\n").encode())

def mpv_load_block(block):
    global endfile_count

    with endfile_lock:
        endfile_count = 0

    mpv_cmd({"command": ["set_property", "pause", True]})
    mpv_cmd({"command": ["playlist-clear"]})

    for v in block:
        mpv_cmd({"command": ["playlist-append", str(v)]})

    mpv_cmd({"command": ["set_property", "playlist-pos", 0]})
    mpv_cmd({"command": ["set_property", "pause", False]})

def mpv_events():
    global endfile_count
    while True:
        try:
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
                s.connect(IPC_SOCKET)
                buf = b""
                while True:
                    buf += s.recv(4096)
                    while b"\n" in buf:
                        line, buf = buf.split(b"\n", 1)
                        evt = json.loads(line.decode(errors="ignore"))
                        if evt.get("event") == "end-file":
                            with endfile_lock:
                                endfile_count += 1
        except:
            time.sleep(0.2)

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
            "--loop-file=inf",
            "--audio-display=no",
            str(audio)
        ])
        time.sleep(1)

# =======================
# MAIN
# =======================

def main():
    threading.Thread(target=audio_loop, daemon=True).start()

    start_mpv()
    threading.Thread(target=mpv_events, daemon=True).start()

    cats = all_categories()
    log(f"Leader activo | categorías: {len(cats)}")

    while True:
        random.shuffle(cats)
        for cat in cats:
            block = pick_block(cat)
            if not block:
                continue

            log(f"▶ Reproduciendo categoría: {cat}")
            mpv_load_block(block)

            while True:
                with endfile_lock:
                    if endfile_count >= BLOCK_SIZE:
                        break
                time.sleep(0.05)

if __name__ == "__main__":
    main()
