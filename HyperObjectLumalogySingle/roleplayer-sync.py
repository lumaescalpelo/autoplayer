#!/usr/bin/env python3
# -------------------------------------------------------
# Raspberry Pi 4B+ — RolePlayer IPC (mpv persistente)
# - mpv de video: 1 sola vez, IPC JSON, sin flashes de escritorio
# - audio: mpv separado por ROLE en loop infinito
# - leader decide categoría; todos reproducen la misma categoría
# - cada nodo elige 4 videos aleatorios dentro de esa categoría:
#     1 con texto + 3 sin texto
# - cambio de categoría: "primero que termina" (followers mandan DONE)
#   leader avanza si recibe DONE o si termina sin DONE (asume que fue el más corto)
# - CORRECCIÓN CLAVE: el leader reproduce localmente incluso si está solo
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

ROLE = 0  # 0 = leader, 1..3 = followers
ORIENTATION = "hor"  # hor | ver | inverted_hor | inverted_ver

BASE_VIDEO_DIR = Path.home() / "Videos" / "videos_hd_final"
BASE_AUDIO_DIR = Path.home() / "Music" / "audios"
VIDEO_EXTENSIONS = (".mp4", ".mov", ".mkv")

# Networking
CMD_PORT  = 9001   # leader -> all : PLAY:<cat>
DONE_PORT = 9100   # follower -> leader: DONE:<cat>
BCAST_PORT = 8888  # leader heartbeat

# IPC (socket UNIX)
IPC_SOCKET = f"/tmp/mpv_roleplayer_{ROLE}.sock"

# Bloque fijo
BLOCK_SIZE = 4  # 1 texto + 3 sin texto

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
        "rotation": 0,   # vertical ya rotado en archivo
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

leader_ip: Optional[str] = None
leader_lock = threading.Lock()

# leader: primer DONE recibido para la categoría actual
first_done_evt = threading.Event()

# mpv events: contador de end-file del bloque actual
endfile_lock = threading.Lock()
endfile_count = 0

# para evitar carreras al recargar playlist
playlist_lock = threading.Lock()

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

# =======================
# MPV IPC (COMMANDS)
# =======================

def start_mpv_video():
    # Limpia socket viejo
    if os.path.exists(IPC_SOCKET):
        os.remove(IPC_SOCKET)

    rot = ORIENTATION_MAP[ORIENTATION]["rotation"]

    # mpv persistente, idle, fullscreen, IPC
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

    # Espera socket
    for _ in range(80):
        if os.path.exists(IPC_SOCKET):
            log("MPV", f"IPC listo: {IPC_SOCKET}")
            return
        time.sleep(0.1)
    raise RuntimeError("mpv IPC socket no apareció (revisa que mpv esté instalado y corriendo en X11)")

def mpv_send(obj: dict) -> None:
    payload = (json.dumps(obj) + "\n").encode("utf-8")
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
        s.connect(IPC_SOCKET)
        s.sendall(payload)

def mpv_clear_playlist():
    mpv_send({"command": ["playlist-clear"]})

def mpv_append(path: Path):
    # playlist-append maneja bien rutas con espacios
    mpv_send({"command": ["playlist-append", str(path)]})

def mpv_set_pause(paused: bool):
    mpv_send({"command": ["set_property", "pause", paused]})

def mpv_set_playlist_pos(idx: int):
    mpv_send({"command": ["set_property", "playlist-pos", idx]})

# =======================
# MPV IPC (EVENTS)
# =======================

def mpv_event_listener():
    """
    Escucha eventos del mpv de video.
    Incrementa endfile_count cuando termina un archivo (end-file).
    """
    global endfile_count

    # Observamos eventos sin necesitar observe_property.
    # mpv manda JSON por el socket, uno por línea.
    while True:
        try:
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
                s.connect(IPC_SOCKET)

                buf = b""
                while True:
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
                            with endfile_lock:
                                endfile_count += 1
        except Exception:
            # mpv aún no está listo o se reinició; reintenta
            time.sleep(0.2)

# =======================
# AUDIO (INDEPENDIENTE)
# =======================

def pick_audio() -> Path:
    return BASE_AUDIO_DIR / {
        0: "drone_81.WAV",
        1: "drone_82.WAV",
        2: "drone_83.WAV",
        3: "drone_84.WAV",
    }[ROLE]

def audio_loop():
    while True:
        try:
            subprocess.run([
                "mpv",
                "--no-terminal",
                "--quiet",
                "--loop-file=inf",
                "--audio-display=no",
                str(pick_audio())
            ])
        except Exception:
            pass
        time.sleep(1)

# =======================
# NETWORK (LEADER DISCOVERY)
# =======================

def leader_broadcast_loop():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
    while True:
        try:
            s.sendto(b"LEADER_HERE", ("255.255.255.255", BCAST_PORT))
        except Exception:
            pass
        time.sleep(1)

def follower_listen_leader_loop():
    global leader_ip
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.bind(("", BCAST_PORT))
    while True:
        _, addr = s.recvfrom(1024)
        with leader_lock:
            leader_ip = addr[0]

# =======================
# NETWORK (DONE)
# =======================

def follower_send_done(cat: str):
    with leader_lock:
        lip = leader_ip
    if not lip:
        return
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.sendto(f"DONE:{cat}".encode("utf-8"), (lip, DONE_PORT))
        log("FOLLOWER", f"DONE enviado: {cat}")
    except Exception:
        pass

def leader_listen_done_loop():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.bind(("", DONE_PORT))
    while True:
        data, addr = s.recvfrom(1024)
        msg = data.decode("utf-8", errors="ignore").strip()
        if msg.startswith("DONE:"):
            if not first_done_evt.is_set():
                log("LEADER", f"FIRST DONE desde {addr[0]}")
                first_done_evt.set()

# =======================
# NETWORK (COMMANDS)
# =======================

def leader_send_play(cat: str):
    msg = f"PLAY:{cat}".encode("utf-8")
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        s.sendto(msg, ("255.255.255.255", CMD_PORT))
    log("LEADER", f"PLAY broadcast: {cat}")

def follower_listen_play_loop(on_play_cb):
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.bind(("", CMD_PORT))
    while True:
        data, _ = s.recvfrom(2048)
        msg = data.decode("utf-8", errors="ignore").strip()
        if msg.startswith("PLAY:"):
            cat = msg.split(":", 1)[1]
            on_play_cb(cat)

# =======================
# CORE: LOAD BLOCK INTO MPV (NO CLOSE)
# =======================

def load_block_into_mpv(cat: str) -> bool:
    """
    Limpia playlist y carga 4 archivos (1 texto + 3 sin texto).
    Reset de endfile_count para poder detectar fin del bloque exacto.
    """
    global endfile_count

    with playlist_lock:
        block = pick_block(cat)
        if len(block) != BLOCK_SIZE:
            log("VIDEO", f"Sin material suficiente para {cat}")
            return False

        # Reset contador de end-file para este bloque
        with endfile_lock:
            endfile_count = 0

        # Cargar playlist en mpv sin cerrar
        mpv_set_pause(True)
        mpv_clear_playlist()
        for v in block:
            mpv_append(v)

        # Arrancar desde el inicio del playlist
        mpv_set_playlist_pos(0)
        mpv_set_pause(False)

        return True

def wait_block_finished_or_done(is_leader: bool) -> str:
    """
    Espera a que:
    - terminen 4 archivos (end-file x4), o
    - leader reciba FIRST DONE (solo en leader)
    Retorna "FINISHED" o "PREEMPTED"
    """
    while True:
        if is_leader and first_done_evt.is_set():
            return "PREEMPTED"

        with endfile_lock:
            if endfile_count >= BLOCK_SIZE:
                return "FINISHED"

        time.sleep(0.05)

# =======================
# ROLE LOOPS
# =======================

def follower_run():
    # Descubre leader y escucha PLAY
    threading.Thread(target=follower_listen_leader_loop, daemon=True).start()

    current_cat = {"value": None}

    def on_play(cat: str):
        current_cat["value"] = cat
        log("FOLLOWER", f"PLAY recibido: {cat}")
        ok = load_block_into_mpv(cat)
        if not ok:
            return
        # Espera fin exacto del bloque (4 end-file)
        res = wait_block_finished_or_done(is_leader=False)
        if res == "FINISHED":
            follower_send_done(cat)

    threading.Thread(target=follower_listen_play_loop, args=(on_play,), daemon=True).start()
    log("FOLLOWER", f"activo | ROLE={ROLE} | ORIENTATION={ORIENTATION}")

    while True:
        time.sleep(1)

def leader_run():
    cats = all_categories()
    if not cats:
        log("LEADER", f"No hay categorías en {BASE_VIDEO_DIR}")
        while True:
            time.sleep(1)

    threading.Thread(target=leader_broadcast_loop, daemon=True).start()
    threading.Thread(target=leader_listen_done_loop, daemon=True).start()

    log("LEADER", f"activo | ROLE={ROLE} | ORIENTATION={ORIENTATION}")

    while True:
        first_done_evt.clear()

        # Decide categoría global
        cat = random.choice(cats)

        # Orden a todos
        leader_send_play(cat)

        # ✅ CORRECCIÓN: el leader reproduce localmente SIN depender de red
        ok = load_block_into_mpv(cat)
        if not ok:
            time.sleep(0.2)
            continue

        res = wait_block_finished_or_done(is_leader=True)

        if res == "PREEMPTED":
            # alguien terminó primero -> cambiar ya
            log("LEADER", "PREEMPTED por FIRST DONE -> avanzando")
            continue

        # FINISHED: leader terminó y no llegó DONE externo antes
        log("LEADER", "Leader terminó sin DONE externo -> avanzando")

# =======================
# MAIN
# =======================

def main():
    # Audio siempre
    threading.Thread(target=audio_loop, daemon=True).start()

    # mpv persistente + events
    start_mpv_video()
    threading.Thread(target=mpv_event_listener, daemon=True).start()

    if ROLE == 0:
        leader_run()
    else:
        follower_run()

if __name__ == "__main__":
    main()
