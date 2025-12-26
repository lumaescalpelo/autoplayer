#!/usr/bin/env python3
# -------------------------------------------------------
# Raspberry Pi 4B+ — Autoplayer mpv
# BLOQUES ESTABLES DE 4 VIDEOS
# Standalone robusto + sincronización UDP por categoría
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

ROLE = 0                  # 0 = leader, 1..3 followers
ORIENTATION = "hor"       # hor | ver | inverted_hor | inverted_ver
ROUNDS = 100

BASE_VIDEO_DIR = Path.home() / "Videos" / "videos_hd_final"
BASE_AUDIO_DIR = Path.home() / "Music" / "audios"
VIDEO_EXTENSIONS = (".mp4", ".mov", ".mkv")

# UDP
BCAST_PORT = 8888
CMD_PORT   = 9001
DONE_PORT  = 9100
LEADER_TIMEOUT = 5.0

# MPV / IPC
IPC_SOCKET = f"/tmp/mpv_roleplayer_{ROLE}.sock"
BLOCK_SIZE = 4

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
        "rotation": 0,  # videos ya rotados
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
# GLOBAL STATE
# =======================

stop_evt = threading.Event()

mode_lock = threading.Lock()
mode = "STANDALONE"   # STANDALONE | SYNCED

leader_ip: Optional[str] = None
last_leader_seen = 0.0

category_lock = threading.Lock()
current_category: Optional[str] = None

# =======================
# LOG
# =======================

def log(msg: str):
    print(msg, flush=True)

# =======================
# FILES / CATEGORIES
# =======================

def is_video(p: Path) -> bool:
    return p.is_file() and p.suffix.lower() in VIDEO_EXTENSIONS

def category_dirs(cat: str):
    cfg = ORIENTATION_MAP[ORIENTATION]
    return (
        BASE_VIDEO_DIR / cat / cfg["text_dir"],
        BASE_VIDEO_DIR / cat / cfg["video_dir"],
    )

def pick_block(cat: str) -> List[Path]:
    """
    Devuelve SIEMPRE 4 videos:
    - 1 con texto
    - 3 sin texto
    - orden aleatorio
    """
    text_dir, vid_dir = category_dirs(cat)

    textos = [p for p in text_dir.iterdir() if is_video(p)] if text_dir.exists() else []
    vids   = [p for p in vid_dir.iterdir() if is_video(p)] if vid_dir.exists() else []

    if len(textos) < 1 or len(vids) < 3:
        return []

    block = [
        random.choice(textos),
        *random.sample(vids, 3),
    ]
    random.shuffle(block)
    return block

def all_categories() -> List[str]:
    if not BASE_VIDEO_DIR.exists():
        return []
    return [d.name for d in BASE_VIDEO_DIR.iterdir() if d.is_dir()]

def build_category_playlist() -> List[str]:
    cats = all_categories()
    out: List[str] = []
    for _ in range(ROUNDS):
        random.shuffle(cats)
        out.extend(cats)
    return out

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
# MPV IPC
# =======================

class MPVIPC:
    def __init__(self, sock_path: str):
        self.sock_path = sock_path
        self.sock: Optional[socket.socket] = None
        self.lock = threading.Lock()
        self.req_id = 0
        self.responses = {}
        self.cv = threading.Condition()

    def connect(self):
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        s.connect(self.sock_path)
        self.sock = s
        threading.Thread(target=self._reader, daemon=True).start()

    def _reader(self):
        buf = b""
        while not stop_evt.is_set():
            try:
                data = self.sock.recv(4096)
                if not data:
                    time.sleep(0.05)
                    continue
                buf += data
                while b"\n" in buf:
                    line, buf = buf.split(b"\n", 1)
                    msg = json.loads(line.decode(errors="ignore"))
                    if "request_id" in msg:
                        with self.cv:
                            self.responses[msg["request_id"]] = msg
                            self.cv.notify_all()
            except:
                time.sleep(0.05)

    def cmd(self, command: List, timeout=3.0):
        with self.lock:
            self.req_id += 1
            rid = self.req_id
            payload = {"command": command, "request_id": rid}
            self.sock.sendall((json.dumps(payload) + "\n").encode())

        deadline = time.time() + timeout
        with self.cv:
            while time.time() < deadline:
                if rid in self.responses:
                    return self.responses.pop(rid)
                self.cv.wait(0.1)
        return {"error": "timeout"}

# =======================
# MPV START
# =======================

def kill_old_mpv():
    try:
        subprocess.run(["pkill", "-9", "mpv"], stdout=subprocess.DEVNULL)
    except:
        pass
    try:
        os.remove(IPC_SOCKET)
    except:
        pass

def start_mpv() -> MPVIPC:
    kill_old_mpv()

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

    ipc = MPVIPC(IPC_SOCKET)
    ipc.connect()
    return ipc

# =======================
# MPV BLOCK PLAY (ESTABLE)
# =======================

def mpv_play_block(ipc: MPVIPC, block: List[Path]):
    ipc.cmd(["set_property", "pause", True])
    ipc.cmd(["playlist-clear"])

    ipc.cmd(["loadfile", str(block[0]), "replace"])
    for p in block[1:]:
        ipc.cmd(["loadfile", str(p), "append"])

    ipc.cmd(["set_property", "playlist-pos", 0])
    ipc.cmd(["set_property", "pause", False])

def wait_block_done(ipc: MPVIPC):
    """
    Espera al FINAL REAL del bloque:
    último item + idle
    """
    while not stop_evt.is_set():
        pos = ipc.cmd(["get_property", "playlist-pos"]).get("data")
        count = ipc.cmd(["get_property", "playlist-count"]).get("data")
        idle = ipc.cmd(["get_property", "idle-active"]).get("data")

        if (
            isinstance(pos, int)
            and isinstance(count, int)
            and count > 0
            and pos == count - 1
            and idle is True
        ):
            return

        time.sleep(0.1)

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
    global leader_ip, mode
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.bind(("", BCAST_PORT))
    while not stop_evt.is_set():
        data, addr = s.recvfrom(1024)
        if data == b"LEADER_HERE" and ROLE != 0:
            leader_ip = addr[0]
            with mode_lock:
                mode = "SYNCED"

def udp_listen_cmd():
    global current_category
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.bind(("", CMD_PORT))
    while not stop_evt.is_set():
        data, _ = s.recvfrom(1024)
        msg = data.decode()
        if msg.startswith("PLAY:"):
            with category_lock:
                current_category = msg.split(":", 1)[1]

def udp_send_done():
    if leader_ip:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.sendto(b"DONE", (leader_ip, DONE_PORT))

# =======================
# MAIN PLAYBACK LOOP
# =======================

def playback_loop(ipc: MPVIPC):
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
        if len(block) != BLOCK_SIZE:
            continue

        log(f"[ROLE {ROLE}] ▶ {cat}")
        mpv_play_block(ipc, block)
        wait_block_done(ipc)

        if m == "SYNCED" and ROLE != 0:
            udp_send_done()

        if ROLE == 0 and m == "SYNCED":
            next_cat = random.choice(all_categories())
            with category_lock:
                current_category = next_cat
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
            s.sendto(f"PLAY:{next_cat}".encode(), ("255.255.255.255", CMD_PORT))

# =======================
# MAIN
# =======================

def main():
    signal.signal(signal.SIGINT, lambda *_: stop_evt.set())
    signal.signal(signal.SIGTERM, lambda *_: stop_evt.set())

    threading.Thread(target=audio_loop, daemon=True).start()

    ipc = start_mpv()

    threading.Thread(target=udp_listen_leader, daemon=True).start()
    threading.Thread(target=udp_listen_cmd, daemon=True).start()

    if ROLE == 0:
        threading.Thread(target=udp_broadcast_loop, daemon=True).start()

    playback_loop(ipc)

if __name__ == "__main__":
    main()
