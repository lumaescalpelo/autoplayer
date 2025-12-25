#!/usr/bin/env python3
# -------------------------------------------------------
# Raspberry Pi 4B+ — Autoplayer sincronizado (Leader/Follower)
# -------------------------------------------------------

import random
import socket
import subprocess
import time
import threading
from pathlib import Path

# =======================
# CONFIG
# =======================

ROLE = 0  # 0 = leader, 1..3 followers
ORIENTATION = "inverted_ver"  # hor | ver | inverted_hor | inverted_ver

ROUNDS = 100  # prácticamente infinito

BASE_VIDEO_DIR = Path.home() / "Videos" / "videos_hd_final"
BASE_AUDIO_DIR = Path.home() / "Music" / "audios"

VIDEO_EXTENSIONS = (".mp4", ".mov", ".mkv")

# NETWORK
BCAST_PORT = 8888
CMD_PORT   = 9001
DONE_PORT  = 9100

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
        "rotation": 0,   # ya vienen rotados
        "text_dir": "ver_rotated_text",
        "video_dir": "ver_rotated",
    },
    "inverted_hor": {
        "rotation": 180,
        "text_dir": "hor_text",
        "video_dir": "hor",
    },
    "inverted_ver": {
        "rotation": 180,
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

def audio_loop():
    proc = None
    while True:
        if proc is None or proc.poll() is not None:
            proc = subprocess.Popen([
                "mpv", "--no-terminal",
                "--loop-file=inf",
                "--audio-display=no",
                str(pick_audio())
            ])
        time.sleep(1)

# =======================
# VIDEO UTILS
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
    textos = list(text_dir.glob("*")) if text_dir.exists() else []
    vids   = list(vid_dir.glob("*")) if vid_dir.exists() else []

    textos = [p for p in textos if is_video(p)]
    vids   = [p for p in vids if is_video(p)]

    if not textos or len(vids) < 3:
        return None

    return [random.choice(textos)] + random.sample(vids, 3)

def play_block(cat: str):
    block = pick_block(cat)
    if not block:
        return

    rotation = ORIENTATION_MAP[ORIENTATION]["rotation"]

    for video in block:
        subprocess.run([
            "mpv",
            "--fs",
            "--force-window=yes",
            "--keep-open=no",
            "--hwdec=auto-safe",
            "--vo=gpu",
            "--scale=bilinear",
            f"--video-rotate={rotation}",
            "--panscan=1.0",
            str(video)
        ])

# =======================
# NETWORKING
# =======================

followers = set()
followers_lock = threading.Lock()

def leader_broadcast_loop():
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)

    while True:
        sock.sendto(b"LEADER_HERE", ("255.255.255.255", BCAST_PORT))
        time.sleep(1)

def listen_broadcast_loop():
    global leader_ip
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind(("", BCAST_PORT))
    while True:
        _, addr = sock.recvfrom(1024)
        leader_ip = addr[0]

def listen_done_loop():
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind(("", DONE_PORT))
    while True:
        _, addr = sock.recvfrom(1024)
        with followers_lock:
            if followers:
                send_next_category()

def send_next_category():
    cat = random.choice([d.name for d in BASE_VIDEO_DIR.iterdir() if d.is_dir()])
    msg = f"PLAY:{cat}".encode()

    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        s.sendto(msg, ("255.255.255.255", CMD_PORT))

def listen_commands_loop():
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind(("", CMD_PORT))

    while True:
        data, _ = sock.recvfrom(2048)
        msg = data.decode()

        if msg.startswith("PLAY:"):
            cat = msg.split(":", 1)[1]
            play_block(cat)
            send_done()

def send_done():
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        s.sendto(b"DONE", (leader_ip, DONE_PORT))

# =======================
# MAIN
# =======================

def main():
    threading.Thread(target=audio_loop, daemon=True).start()

    if ROLE == 0:
        print("👑 Leader activo")
        threading.Thread(target=leader_broadcast_loop, daemon=True).start()
        threading.Thread(target=listen_done_loop, daemon=True).start()

        while True:
            send_next_category()
            time.sleep(999)

    else:
        print("👥 Follower activo")
        threading.Thread(target=listen_broadcast_loop, daemon=True).start()
        threading.Thread(target=listen_commands_loop, daemon=True).start()
        while True:
            time.sleep(1)

if __name__ == "__main__":
    main()
