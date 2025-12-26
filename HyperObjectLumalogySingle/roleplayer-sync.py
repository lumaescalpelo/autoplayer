#!/usr/bin/env python3
# -------------------------------------------------------
# Raspberry Pi 4B+ — Autoplayer robusto + UDP autosync
# -------------------------------------------------------

import json
import os
import random
import socket
import subprocess
import threading
import time
import signal
from pathlib import Path
from typing import List, Optional

# =======================
# CONFIG
# =======================

ROLE = 0                  # 0=leader, 1..3=follower
ORIENTATION = "hor"
ROUNDS = 100

BASE_VIDEO_DIR = Path.home() / "Videos" / "videos_hd_final"
BASE_AUDIO_DIR = Path.home() / "Music" / "audios"
VIDEO_EXTENSIONS = (".mp4", ".mov", ".mkv")

# UDP
BCAST_PORT = 8888
CMD_PORT   = 9001
DONE_PORT  = 9100
LEADER_TIMEOUT = 5.0

# IPC
IPC_SOCKET = f"/tmp/mpv_roleplayer_{ROLE}.sock"
BLOCK_SIZE = 4

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
# GLOBAL STATE
# =======================

mode_lock = threading.Lock()
mode = "STANDALONE"     # STANDALONE | SYNCED

leader_ip = None
last_leader_seen = 0.0

current_category = None
category_lock = threading.Lock()

stop_evt = threading.Event()

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

def category_dirs(cat):
    cfg = ORIENTATION_MAP[ORIENTATION]
    return (
        BASE_VIDEO_DIR / cat / cfg["text_dir"],
        BASE_VIDEO_DIR / cat / cfg["video_dir"],
    )

def pick_block(cat):
    text_dir, vid_dir = category_dirs(cat)
    textos = [p for p in text_dir.iterdir() if is_video(p)] if text_dir.exists() else []
    vids   = [p for p in vid_dir.iterdir() if is_video(p)] if vid_dir.exists() else []
    if not textos or len(vids) < 3:
        return []
    return [random.choice(textos)] + random.sample(vids, 3)

def all_categories():
    return [d.name for d in BASE_VIDEO_DIR.iterdir() if d.is_dir()]

def build_category_playlist():
    cats = all_categories()
    out = []
    for _ in range(ROUNDS):
        random.shuffle(cats)
        out.extend(cats)
    return out

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
    audio = pick_audio()
    while not stop_evt.is_set():
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
# MPV IPC (reutilizamos núcleo estable)
# =======================

# ---- (MISMO MPVIPC, start_mpv_video, mpv_load_block, wait_block)
# ---- OMITO COMENTARIOS AQUÍ POR BREVEDAD
# ---- ES EXACTAMENTE EL MISMO NÚCLEO QUE YA PROBASTE
# ---- NO SE MODIFICA

# (Por razones de espacio, asumo que sigues usando
#  EXACTAMENTE las funciones IPC estables del archivo anterior)

# =======================
# UDP NETWORK
# =======================

def udp_broadcast_loop():
    if ROLE != 0:
        return
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
    while not stop_evt.is_set():
        s.sendto(b"LEADER_HERE", ("255.255.255.255", BCAST_PORT))
        time.sleep(1)

def udp_listen_leader():
    global leader_ip, last_leader_seen, mode
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.bind(("", BCAST_PORT))
    while not stop_evt.is_set():
        data, addr = s.recvfrom(1024)
        if data == b"LEADER_HERE":
            leader_ip = addr[0]
            last_leader_seen = time.time()
            with mode_lock:
                if ROLE != 0:
                    mode = "SYNCED"

def udp_listen_commands():
    global current_category
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.bind(("", CMD_PORT))
    while not stop_evt.is_set():
        data, addr = s.recvfrom(1024)
        msg = data.decode()
        if msg.startswith("PLAY:"):
            cat = msg.split(":", 1)[1]
            with category_lock:
                current_category = cat

def udp_send_done():
    if leader_ip:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.sendto(b"DONE", (leader_ip, DONE_PORT))

# =======================
# MAIN PLAYBACK LOOP
# =======================

def playback_loop(ipc):
    global current_category

    local_playlist = build_category_playlist()
    idx = 0

    while not stop_evt.is_set():
        with mode_lock:
            m = mode

        if m == "STANDALONE":
            if idx >= len(local_playlist):
                local_playlist = build_category_playlist()
                idx = 0
            cat = local_playlist[idx]
            idx += 1
        else:
            with category_lock:
                cat = current_category
            if not cat:
                time.sleep(0.1)
                continue

        block = pick_block(cat)
        if not block:
            continue

        log(f"[{ROLE}] ▶ {cat}")
        mpv_load_block(ipc, block)
        ipc.wait_block_done(BLOCK_SIZE)

        if m == "SYNCED" and ROLE != 0:
            udp_send_done()

        if ROLE == 0 and m == "SYNCED":
            # leader decide next
            current_category = random.choice(all_categories())
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.sendto(f"PLAY:{current_category}".encode(), ("255.255.255.255", CMD_PORT))

# =======================
# MAIN
# =======================

def main():
    signal.signal(signal.SIGINT, lambda *_: stop_evt.set())
    signal.signal(signal.SIGTERM, lambda *_: stop_evt.set())

    threading.Thread(target=audio_loop, daemon=True).start()

    ipc = start_mpv_video()

    threading.Thread(target=udp_listen_leader, daemon=True).start()
    threading.Thread(target=udp_listen_commands, daemon=True).start()

    if ROLE == 0:
        threading.Thread(target=udp_broadcast_loop, daemon=True).start()

    playback_loop(ipc)

if __name__ == "__main__":
    main()
