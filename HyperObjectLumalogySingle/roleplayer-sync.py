#!/usr/bin/env python3
# -------------------------------------------------------
# Raspberry Pi 4B+ — Autoplayer SYNC por categoría (UDP)
# - mpv video persistente (IPC JSON), sin flashes de escritorio
# - mpv audio independiente por ROLE
# - Modo AUTO (sin leader): playlist de categorías 100 rondas
# - Modo SYNC (con leader): leader define categoría; todos eligen 4 vids aleatorios
# - DONE (primer fin): fuerza salto a siguiente categoría
# -------------------------------------------------------

import json
import os
import random
import socket
import subprocess
import threading
import time
from pathlib import Path
from typing import List, Optional, Tuple

# =======================
# CONFIG
# =======================

ROLE = 0  # 0=leader, 1..3=follower
ORIENTATION = "hor"  # hor | ver | inverted_hor | inverted_ver
ROUNDS = 100

BASE_VIDEO_DIR = Path.home() / "Videos" / "videos_hd_final"
BASE_AUDIO_DIR = Path.home() / "Music" / "audios"
VIDEO_EXTENSIONS = (".mp4", ".mov", ".mkv")

# UDP ports
BCAST_PORT = 8888      # leader heartbeat: "LEADER_HERE"
REG_PORT   = 8899      # follower -> leader: "REGISTER:<host>:ROLE=<n>"
CMD_PORT   = 9001      # leader -> all: "PLAY:<idx>"
DONE_PORT  = 9100      # follower -> leader: "DONE:<idx>"

LEADER_TIMEOUT = 6.0   # follower: si no ve leader en X segundos, vuelve a AUTO

# IPC socket
IPC_SOCKET = f"/tmp/mpv_roleplayer_{ROLE}.sock"

# reproducción
BLOCK_SIZE = 4  # 1 texto + 3 sin texto
POLL_DT = 0.05

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
        "rotation": 0,  # videos verticales ya vienen rotados
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
# LOG
# =======================

def log(tag: str, msg: str):
    print(f"[{tag}] {msg}", flush=True)

# =======================
# FILES / CATEGORIES
# =======================

def is_video(p: Path) -> bool:
    return p.is_file() and p.suffix.lower() in VIDEO_EXTENSIONS

def category_dirs(cat: str) -> Tuple[Path, Path]:
    cfg = ORIENTATION_MAP[ORIENTATION]
    return (
        BASE_VIDEO_DIR / cat / cfg["text_dir"],
        BASE_VIDEO_DIR / cat / cfg["video_dir"],
    )

def pick_block(cat: str) -> List[Path]:
    text_dir, vid_dir = category_dirs(cat)
    textos = [p for p in text_dir.iterdir() if is_video(p)] if text_dir.exists() else []
    vids   = [p for p in vid_dir.iterdir() if is_video(p)] if vid_dir.exists() else []

    if not textos or len(vids) < 3:
        return []
    return [random.choice(textos)] + random.sample(vids, 3)

def all_categories() -> List[str]:
    if not BASE_VIDEO_DIR.exists():
        return []
    return [d.name for d in BASE_VIDEO_DIR.iterdir() if d.is_dir()]

def build_category_playlist() -> List[str]:
    cats = all_categories()
    if not cats:
        return []
    out: List[str] = []
    for _ in range(ROUNDS):
        random.shuffle(cats)
        out.extend(cats)
    return out

# =======================
# AUDIO (independiente)
# =======================

def pick_audio() -> Path:
    return BASE_AUDIO_DIR / {
        0: "drone_81.WAV",
        1: "drone_82.WAV",
        2: "drone_83.WAV",
        3: "drone_84.WAV",
    }[ROLE]

def audio_loop(stop_evt: threading.Event):
    audio = pick_audio()
    while not stop_evt.is_set():
        try:
            subprocess.run([
                "mpv",
                "--no-terminal",
                "--quiet",
                "--vd=no",
                "--loop-file=inf",
                "--audio-display=no",
                str(audio)
            ])
        except Exception:
            pass
        time.sleep(1)

# =======================
# MPV VIDEO IPC (persistente)
# =======================

class MPVController:
    def __init__(self, sock_path: str):
        self.sock_path = sock_path
        self.cmd_lock = threading.Lock()

        self.endfile_lock = threading.Lock()
        self.endfile_count = 0

        self.block_lock = threading.Lock()
        self.block_target = BLOCK_SIZE

        self._stop_evt = threading.Event()

    def start_mpv(self):
        if os.path.exists(self.sock_path):
            os.remove(self.sock_path)

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
            f"--input-ipc-server={self.sock_path}",
        ])

        # espera socket
        for _ in range(120):
            if os.path.exists(self.sock_path):
                break
            time.sleep(0.1)
        else:
            raise RuntimeError("mpv IPC no apareció")

        # arranca listener de eventos
        threading.Thread(target=self._event_loop, daemon=True).start()
        log("MPV", "mpv video listo + listener eventos activo")

    def stop(self):
        self._stop_evt.set()

    def _send_cmd(self, cmd_obj: dict):
        data = (json.dumps(cmd_obj) + "\n").encode("utf-8")
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        s.connect(self.sock_path)
        s.sendall(data)
        s.close()

    def cmd(self, command: list):
        # fire-and-forget (más estable; no dependemos de respuestas)
        with self.cmd_lock:
            self._send_cmd({"command": command})

    def _event_loop(self):
        """
        Conexión persistente que recibe eventos JSON.
        Contamos end-file para saber cuándo termina un bloque de 4.
        """
        buf = b""
        while not self._stop_evt.is_set():
            try:
                s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                s.connect(self.sock_path)
                s.settimeout(1.0)

                while not self._stop_evt.is_set():
                    try:
                        chunk = s.recv(4096)
                        if not chunk:
                            break
                        buf += chunk
                        while b"\n" in buf:
                            line, buf = buf.split(b"\n", 1)
                            if not line.strip():
                                continue
                            try:
                                evt = json.loads(line.decode("utf-8", errors="ignore"))
                            except Exception:
                                continue
                            if evt.get("event") == "end-file":
                                with self.endfile_lock:
                                    self.endfile_count += 1
                    except socket.timeout:
                        continue
                s.close()
            except Exception:
                time.sleep(0.2)

    def reset_endfile_counter(self):
        with self.endfile_lock:
            self.endfile_count = 0

    def wait_block_done(self) -> bool:
        """
        Espera a que terminen BLOCK_SIZE videos.
        """
        while True:
            with self.endfile_lock:
                if self.endfile_count >= BLOCK_SIZE:
                    return True
            time.sleep(POLL_DT)

    def load_block(self, block: List[Path]):
        """
        Carga 4 videos como playlist en mpv SIN cerrar.
        IMPORTANTÍSIMO: usar append (NO append-play) para que suene en orden.
        """
        if len(block) != BLOCK_SIZE:
            return False

        self.reset_endfile_counter()

        # pausar y limpiar playlist
        self.cmd(["set_property", "pause", True])
        self.cmd(["playlist-clear"])

        # 1) replace
        self.cmd(["loadfile", str(block[0]), "replace"])
        # 2) append (no play)
        for p in block[1:]:
            self.cmd(["loadfile", str(p), "append"])

        # asegurar arrancar desde el primero
        self.cmd(["set_property", "playlist-pos", 0])
        self.cmd(["set_property", "pause", False])
        return True

# =======================
# NETWORK STATE
# =======================

leader_ip_lock = threading.Lock()
leader_ip: Optional[str] = None
last_leader_seen = 0.0

followers_lock = threading.Lock()
followers = {}  # ip -> last_seen

# leader: primer DONE recibido para idx actual
first_done_evt = threading.Event()

# =======================
# NETWORK (LEADER)
# =======================

def leader_broadcast_loop(stop_evt: threading.Event):
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
    while not stop_evt.is_set():
        try:
            s.sendto(b"LEADER_HERE", ("255.255.255.255", BCAST_PORT))
        except Exception:
            pass
        time.sleep(1)

def leader_listen_register_loop(stop_evt: threading.Event):
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.bind(("", REG_PORT))
    while not stop_evt.is_set():
        data, addr = s.recvfrom(1024)
        msg = data.decode("utf-8", errors="ignore").strip()
        if msg.startswith("REGISTER:"):
            ip = addr[0]
            with followers_lock:
                followers[ip] = time.time()
            log("LEADER", f"REGISTER {ip} :: {msg}")

def leader_listen_done_loop(stop_evt: threading.Event, current_idx_ref):
    """
    DONE:<idx> desde followers.
    Marca first_done_evt si coincide con idx actual.
    """
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.bind(("", DONE_PORT))
    while not stop_evt.is_set():
        data, addr = s.recvfrom(1024)
        msg = data.decode("utf-8", errors="ignore").strip()
        if msg.startswith("DONE:"):
            try:
                didx = int(msg.split(":", 1)[1])
            except:
                continue
            if didx == current_idx_ref["idx"]:
                if not first_done_evt.is_set():
                    log("LEADER", f"FIRST DONE idx={didx} from {addr[0]}")
                    first_done_evt.set()

def leader_broadcast_play(idx: int):
    msg = f"PLAY:{idx}".encode("utf-8")
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
    s.sendto(msg, ("255.255.255.255", CMD_PORT))
    s.close()

# =======================
# NETWORK (FOLLOWER)
# =======================

def follower_discover_leader_loop(stop_evt: threading.Event):
    global leader_ip, last_leader_seen
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.bind(("", BCAST_PORT))
    while not stop_evt.is_set():
        data, addr = s.recvfrom(1024)
        msg = data.decode("utf-8", errors="ignore")
        if msg.startswith("LEADER_HERE"):
            with leader_ip_lock:
                leader_ip = addr[0]
                last_leader_seen = time.time()

def follower_register_once():
    with leader_ip_lock:
        lip = leader_ip
    if not lip:
        return
    try:
        host = socket.gethostname()
        msg = f"REGISTER:{host}:ROLE={ROLE}".encode("utf-8")
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.sendto(msg, (lip, REG_PORT))
        s.close()
    except Exception:
        pass

def follower_listen_cmd_loop(stop_evt: threading.Event, on_play):
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.bind(("", CMD_PORT))
    while not stop_evt.is_set():
        data, _ = s.recvfrom(1024)
        msg = data.decode("utf-8", errors="ignore").strip()
        if msg.startswith("PLAY:"):
            try:
                idx = int(msg.split(":", 1)[1])
            except:
                continue
            on_play(idx)

def follower_send_done(idx: int):
    with leader_ip_lock:
        lip = leader_ip
    if not lip:
        return
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.sendto(f"DONE:{idx}".encode("utf-8"), (lip, DONE_PORT))
        s.close()
        log("FOLLOWER", f"DONE idx={idx}")
    except Exception:
        pass

# =======================
# MODES
# =======================

def leader_main(stop_evt: threading.Event, mpv: MPVController):
    playlist = build_category_playlist()
    if not playlist:
        log("LEADER", f"No hay categorías en {BASE_VIDEO_DIR}")
        while True:
            time.sleep(1)

    current_idx_ref = {"idx": 0}

    # network threads
    threading.Thread(target=leader_broadcast_loop, args=(stop_evt,), daemon=True).start()
    threading.Thread(target=leader_listen_register_loop, args=(stop_evt,), daemon=True).start()
    threading.Thread(target=leader_listen_done_loop, args=(stop_evt, current_idx_ref), daemon=True).start()

    log("LEADER", f"Playlist categorías creada: {len(playlist)} (ROUNDS={ROUNDS})")

    while not stop_evt.is_set():
        idx = current_idx_ref["idx"]
        if idx >= len(playlist):
            playlist = build_category_playlist()
            current_idx_ref["idx"] = 0
            idx = 0
            log("LEADER", f"Rebuild playlist categorías: {len(playlist)}")

        cat = playlist[idx]
        block = pick_block(cat)
        if len(block) != BLOCK_SIZE:
            log("LEADER", f"Skip cat sin 1+3: {cat}")
            current_idx_ref["idx"] += 1
            continue

        first_done_evt.clear()

        # orden a todos (aunque no haya nadie)
        leader_broadcast_play(idx)
        log("LEADER", f"PLAY idx={idx} cat={cat}")

        # reproduce local (siempre)
        ok = mpv.load_block(block)
        if not ok:
            log("LEADER", "load_block falló")
            time.sleep(0.2)
            continue

        # espera: o DONE externo, o termina su bloque
        while True:
            if first_done_evt.is_set():
                log("LEADER", "DONE externo primero -> NEXT")
                break
            with mpv.endfile_lock:
                if mpv.endfile_count >= BLOCK_SIZE:
                    log("LEADER", "Leader terminó primero -> NEXT")
                    break
            time.sleep(POLL_DT)

        current_idx_ref["idx"] += 1

def follower_main(stop_evt: threading.Event, mpv: MPVController):
    playlist = build_category_playlist()
    if not playlist:
        log("FOLLOWER", f"No hay categorías en {BASE_VIDEO_DIR}")
        while True:
            time.sleep(1)

    mode = {"m": "AUTO"}  # AUTO | SYNC
    current_idx = {"idx": 0}

    def play_idx(idx: int):
        # SYNC: el leader manda el idx; usamos el mismo playlist local
        if idx < 0 or idx >= len(playlist):
            return
        mode["m"] = "SYNC"
        current_idx["idx"] = idx
        cat = playlist[idx]
        block = pick_block(cat)
        if len(block) != BLOCK_SIZE:
            log("FOLLOWER", f"SYNC cat inválida: {cat}")
            return
        log("FOLLOWER", f"SYNC PLAY idx={idx} cat={cat}")
        mpv.load_block(block)

        # al terminar, manda DONE
        mpv.wait_block_done()
        follower_send_done(idx)

    # discover leader + cmd listener
    threading.Thread(target=follower_discover_leader_loop, args=(stop_evt,), daemon=True).start()
    threading.Thread(target=follower_listen_cmd_loop, args=(stop_evt, play_idx), daemon=True).start()

    log("FOLLOWER", f"AUTO playlist categorías: {len(playlist)} (ROUNDS={ROUNDS})")

    # loop principal: AUTO si no hay leader
    while not stop_evt.is_set():
        # detect leader presence
        with leader_ip_lock:
            lip = leader_ip
            seen = last_leader_seen

        if lip and (time.time() - seen) < LEADER_TIMEOUT:
            # leader presente
            follower_register_once()
            # si estamos en SYNC, esperamos comandos (no hacemos AUTO)
            time.sleep(0.3)
            continue

        # si no hay leader: AUTO
        mode["m"] = "AUTO"
        idx = current_idx["idx"]
        if idx >= len(playlist):
            playlist = build_category_playlist()
            idx = 0
            current_idx["idx"] = 0

        cat = playlist[idx]
        block = pick_block(cat)
        if len(block) != BLOCK_SIZE:
            current_idx["idx"] += 1
            time.sleep(0.05)
            continue

        log("FOLLOWER", f"AUTO PLAY idx={idx} cat={cat}")
        mpv.load_block(block)
        mpv.wait_block_done()
        current_idx["idx"] += 1

# =======================
# MAIN
# =======================

def main():
    stop_evt = threading.Event()

    # audio independiente
    threading.Thread(target=audio_loop, args=(stop_evt,), daemon=True).start()

    # mpv video persistente
    mpv = MPVController(IPC_SOCKET)
    mpv.start_mpv()

    if ROLE == 0:
        log("SYS", f"ROLE=LEADER ORIENTATION={ORIENTATION}")
        leader_main(stop_evt, mpv)
    else:
        log("SYS", f"ROLE=FOLLOWER({ROLE}) ORIENTATION={ORIENTATION}")
        follower_main(stop_evt, mpv)

if __name__ == "__main__":
    main()
