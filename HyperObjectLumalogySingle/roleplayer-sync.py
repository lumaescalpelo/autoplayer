#!/usr/bin/env python3
# -------------------------------------------------------
# Raspberry Pi 4B+ — Leader/Follower Sync por CATEGORÍA
# mpv persistente (IPC JSON) + audio independiente
#
# - Leader genera playlist de CATEGORÍAS (100 rondas)
# - Followers reciben playlist + índice actual al conectarse
# - Todos reproducen MISMA CATEGORÍA, pero videos aleatorios LOCALES:
#     1 con texto + 3 sin texto
# - El primero que termina su bloque manda DONE(idx)
# - Leader avanza al siguiente idx y manda PLAY_IDX(idx)
# - Leader reproduce SOLO aunque no haya followers
# -------------------------------------------------------

import json
import os
import random
import socket
import subprocess
import threading
import time
from pathlib import Path
from typing import Dict, List, Optional, Tuple

# =======================
# CONFIG
# =======================

ROLE = 0  # 0=leader, 1..3=follower
ORIENTATION = "hor"  # hor | ver | inverted_hor | inverted_ver

ROUNDS = 100  # rondas de categorías (playlist de categorías)

BASE_VIDEO_DIR = Path.home() / "Videos" / "videos_hd_final"
BASE_AUDIO_DIR = Path.home() / "Music" / "audios"
VIDEO_EXTENSIONS = (".mp4", ".mov", ".mkv")

# Networking
BCAST_PORT = 8888          # leader heartbeat
REG_PORT   = 8899          # followers -> leader register (UDP)
CMD_PORT   = 9001          # leader -> all: PLAY_IDX:<idx>
DONE_PORT  = 9100          # followers -> leader: DONE:<idx>
TCP_PORT   = 9300          # leader TCP server: playlist+state

OFFLINE_AFTER = 15.0       # si follower no ve leader (opcional) => modo offline simple

# IPC
IPC_SOCKET = f"/tmp/mpv_roleplayer_{ROLE}.sock"

# Bloque de reproducción por categoría
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

# =======================
# MPV IPC CLIENT (robusto)
# =======================

class MPVIPC:
    def __init__(self, sock_path: str):
        self.sock_path = sock_path
        self._lock = threading.Lock()
        self._req_id = 0

    def _connect(self):
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        s.connect(self.sock_path)
        return s

    def cmd(self, command: List, timeout: float = 1.0) -> Dict:
        """
        Envía comando y espera respuesta JSON.
        Si mpv responde error != success, lo devolvemos.
        """
        with self._lock:
            self._req_id += 1
            rid = self._req_id

            payload = {"command": command, "request_id": rid}
            data = (json.dumps(payload) + "\n").encode("utf-8")

            s = self._connect()
            s.settimeout(timeout)
            s.sendall(data)

            # mpv responde una línea JSON
            buf = b""
            while b"\n" not in buf:
                chunk = s.recv(4096)
                if not chunk:
                    break
                buf += chunk
            s.close()

            try:
                resp = json.loads(buf.decode("utf-8", errors="ignore").strip() or "{}")
            except Exception:
                resp = {"error": "parse_failed", "raw": buf[:200].decode(errors="ignore")}
            return resp

def start_mpv_video():
    if os.path.exists(IPC_SOCKET):
        os.remove(IPC_SOCKET)

    rot = ORIENTATION_MAP[ORIENTATION]["rotation"]

    # mpv persistente en idle con IPC
    subprocess.Popen([
        "mpv",
        "--idle=yes",
        "--fs",
        "--force-window=yes",
        "--keep-open=yes",
        "--no-terminal",
        "--quiet",
        "--hwdec=auto-safe",
        "--vo=gpu",
        "--scale=bilinear",
        f"--video-rotate={rot}",
        "--panscan=1.0",
        "--stop-screensaver=yes",
        f"--input-ipc-server={IPC_SOCKET}",
    ])

    # Espera a que aparezca socket
    for _ in range(120):
        if os.path.exists(IPC_SOCKET):
            break
        time.sleep(0.1)
    else:
        raise RuntimeError("mpv IPC socket no apareció")

    ipc = MPVIPC(IPC_SOCKET)

    # Importante: probar un comando simple para confirmar que mpv ACEPTA IPC
    resp = ipc.cmd(["get_property", "idle-active"], timeout=2.0)
    if resp.get("error") not in (None, "success"):
        log("MPV", f"IPC prueba falló: {resp}")
    else:
        log("MPV", "IPC OK (mpv acepta comandos)")

    return ipc

# Contador de fin de archivos del bloque
endfile_count = 0
endfile_lock = threading.Lock()

def mpv_event_listener(sock_path: str):
    """
    mpv manda eventos a conexiones persistentes.
    Abrimos una conexión y leemos eventos; contamos end-file.
    """
    global endfile_count
    while True:
        try:
            s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            s.connect(sock_path)
            s.settimeout(None)
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
            time.sleep(0.2)

def mpv_load_block(ipc: MPVIPC, block: List[Path]) -> bool:
    """
    Carga el bloque SIN CERRAR mpv, usando loadfile (más robusto).
    1) pausa
    2) loadfile replace (primer video)
    3) loadfile append-play (resto)
    4) play
    """
    global endfile_count

    if len(block) != BLOCK_SIZE:
        return False

    with endfile_lock:
        endfile_count = 0

    # Pausar
    r = ipc.cmd(["set_property", "pause", True], timeout=2.0)
    if r.get("error") not in (None, "success"):
        log("MPV", f"pause failed: {r}")

    # Reemplaza con primer archivo
    r = ipc.cmd(["loadfile", str(block[0]), "replace"], timeout=4.0)
    if r.get("error") not in (None, "success"):
        log("MPV", f"loadfile replace failed: {r}")
        return False

    # Añade resto
    for p in block[1:]:
        r = ipc.cmd(["loadfile", str(p), "append-play"], timeout=4.0)
        if r.get("error") not in (None, "success"):
            log("MPV", f"loadfile append failed: {r}")
            return False

    # Play
    r = ipc.cmd(["set_property", "pause", False], timeout=2.0)
    if r.get("error") not in (None, "success"):
        log("MPV", f"unpause failed: {r}")

    return True

def wait_block_finished() -> None:
    while True:
        with endfile_lock:
            if endfile_count >= BLOCK_SIZE:
                return
        time.sleep(0.05)

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

def audio_loop():
    audio = pick_audio()
    while True:
        try:
            subprocess.run([
                "mpv",
                "--no-terminal",
                "--quiet",
                "--loop-file=inf",
                "--audio-display=no",
                str(audio)
            ])
        except Exception:
            pass
        time.sleep(1)

# =======================
# LEADER STATE (playlist categorías)
# =======================

playlist_id = None
category_playlist: List[str] = []
current_idx = 0

state_lock = threading.Lock()

followers: Dict[str, float] = {}  # ip -> last_seen
followers_lock = threading.Lock()

# =======================
# NETWORK — Leader announce / discovery
# =======================

leader_ip: Optional[str] = None
leader_ip_lock = threading.Lock()
last_leader_seen = 0.0

def leader_broadcast_loop():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
    payload = f"LEADER_HERE:{TCP_PORT}".encode("utf-8")
    while True:
        try:
            s.sendto(payload, ("255.255.255.255", BCAST_PORT))
        except Exception:
            pass
        time.sleep(1)

def follower_discover_leader_loop():
    global leader_ip, last_leader_seen
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.bind(("", BCAST_PORT))
    while True:
        data, addr = s.recvfrom(1024)
        msg = data.decode("utf-8", errors="ignore")
        if msg.startswith("LEADER_HERE:"):
            with leader_ip_lock:
                leader_ip = addr[0]
                last_leader_seen = time.time()

# =======================
# NETWORK — Register + TCP sync
# =======================

def leader_register_listener_loop():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.bind(("", REG_PORT))
    while True:
        data, addr = s.recvfrom(1024)
        msg = data.decode("utf-8", errors="ignore").strip()
        if msg.startswith("REGISTER:"):
            ip = addr[0]
            with followers_lock:
                followers[ip] = time.time()
            log("LEADER", f"REGISTER desde {ip} ({msg})")

def follower_register_once():
    with leader_ip_lock:
        lip = leader_ip
    if not lip:
        return False
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            host = socket.gethostname()
            s.sendto(f"REGISTER:{host}:ROLE={ROLE}".encode("utf-8"), (lip, REG_PORT))
        return True
    except Exception:
        return False

def leader_tcp_server_loop():
    """
    Server TCP simple:
    - follower conecta y manda 'GET\n'
    - leader responde JSON en una sola línea:
      {"playlist_id": "...", "current_idx": N, "categories": [..]}
    """
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(("", TCP_PORT))
    srv.listen(5)
    log("LEADER", f"TCP server en puerto {TCP_PORT}")

    while True:
        conn, addr = srv.accept()
        conn.settimeout(3.0)
        try:
            req = conn.recv(64).decode("utf-8", errors="ignore").strip()
            if not req.startswith("GET"):
                conn.close()
                continue

            with state_lock:
                payload = {
                    "playlist_id": playlist_id,
                    "current_idx": current_idx,
                    "categories": category_playlist,
                }

            line = (json.dumps(payload) + "\n").encode("utf-8")
            conn.sendall(line)
        except Exception:
            pass
        finally:
            conn.close()

def follower_fetch_state() -> Optional[dict]:
    with leader_ip_lock:
        lip = leader_ip
    if not lip:
        return None
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(4.0)
        s.connect((lip, TCP_PORT))
        s.sendall(b"GET\n")
        buf = b""
        while b"\n" not in buf:
            chunk = s.recv(4096)
            if not chunk:
                break
            buf += chunk
        s.close()
        line = buf.split(b"\n", 1)[0].decode("utf-8", errors="ignore")
        return json.loads(line)
    except Exception:
        return None

# =======================
# NETWORK — Commands & DONE
# =======================

def leader_broadcast_play_idx(idx: int):
    msg = f"PLAY_IDX:{idx}".encode("utf-8")
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        s.sendto(msg, ("255.255.255.255", CMD_PORT))

def follower_listen_cmd_loop(on_idx):
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.bind(("", CMD_PORT))
    while True:
        data, _ = s.recvfrom(1024)
        msg = data.decode("utf-8", errors="ignore").strip()
        if msg.startswith("PLAY_IDX:"):
            try:
                idx = int(msg.split(":", 1)[1])
                on_idx(idx)
            except:
                pass

first_done_evt = threading.Event()

def leader_done_listener_loop():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.bind(("", DONE_PORT))
    while True:
        data, addr = s.recvfrom(1024)
        msg = data.decode("utf-8", errors="ignore").strip()
        if msg.startswith("DONE:"):
            try:
                done_idx = int(msg.split(":", 1)[1])
            except:
                continue
            with state_lock:
                if done_idx == current_idx:
                    if not first_done_evt.is_set():
                        log("LEADER", f"FIRST DONE idx={done_idx} desde {addr[0]}")
                        first_done_evt.set()

def follower_send_done(idx: int):
    with leader_ip_lock:
        lip = leader_ip
    if not lip:
        return
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.sendto(f"DONE:{idx}".encode("utf-8"), (lip, DONE_PORT))
        log("FOLLOWER", f"DONE idx={idx}")
    except Exception:
        pass

# =======================
# PLAYLIST DE CATEGORÍAS (leader)
# =======================

def leader_build_category_playlist() -> List[str]:
    cats = all_categories()
    if not cats:
        return []
    out = []
    for _ in range(ROUNDS):
        random.shuffle(cats)
        out.extend(cats)
    return out

# =======================
# RUN LOOPS
# =======================

def leader_run(ipc: MPVIPC):
    global playlist_id, category_playlist, current_idx

    cats_pl = leader_build_category_playlist()
    if not cats_pl:
        log("LEADER", f"No hay categorías en {BASE_VIDEO_DIR}")
        while True:
            time.sleep(1)

    playlist_id = f"pl_{int(time.time())}_{random.randint(1000,9999)}"
    category_playlist = cats_pl
    current_idx = 0

    log("LEADER", f"Playlist categorías creada: {len(category_playlist)} items (ROUNDS={ROUNDS})")

    # Threads de red
    threading.Thread(target=leader_broadcast_loop, daemon=True).start()
    threading.Thread(target=leader_register_listener_loop, daemon=True).start()
    threading.Thread(target=leader_tcp_server_loop, daemon=True).start()
    threading.Thread(target=leader_done_listener_loop, daemon=True).start()

    # Leader reproduce SOLO siempre (sin depender de followers)
    while True:
        first_done_evt.clear()

        with state_lock:
            idx = current_idx
            if idx >= len(category_playlist):
                # reiniciar con nueva playlist
                playlist_id = f"pl_{int(time.time())}_{random.randint(1000,9999)}"
                category_playlist = leader_build_category_playlist()
                current_idx = 0
                idx = 0
                log("LEADER", f"Rebuild playlist categorías: {len(category_playlist)}")

            cat = category_playlist[idx]

        # Orden a todos (si no hay nadie, no pasa nada)
        leader_broadcast_play_idx(idx)

        # Reproducción local del leader (siempre)
        block = pick_block(cat)
        if len(block) != BLOCK_SIZE:
            log("LEADER", f"Cat inválida (sin 1+3): {cat} -> skip")
            with state_lock:
                current_idx += 1
            continue

        log("LEADER", f"PLAY idx={idx} cat={cat} (local block aleatorio)")
        ok = mpv_load_block(ipc, block)
        if not ok:
            log("LEADER", "mpv_load_block falló (ver logs MPV arriba)")
            time.sleep(0.2)
            continue

        # Espera: o alguien manda DONE primero, o el leader termina sus 4
        while True:
            if first_done_evt.is_set():
                # preempt: avanzar ya
                log("LEADER", "PREEMPTED por DONE externo -> next idx")
                break
            with endfile_lock:
                if endfile_count >= BLOCK_SIZE:
                    log("LEADER", "Leader terminó su bloque sin DONE externo -> next idx")
                    break
            time.sleep(0.05)

        with state_lock:
            current_idx += 1

def follower_run(ipc: MPVIPC):
    """
    Follower:
    - descubre leader por broadcast
    - se registra
    - si no tiene playlist/estado, lo pide por TCP
    - escucha PLAY_IDX y reproduce esa categoría con bloque aleatorio local
    - al terminar, manda DONE(idx)
    - si leader cae, puede seguir en 'offline' con su playlist (opcional básico)
    """
    threading.Thread(target=follower_discover_leader_loop, daemon=True).start()

    local_playlist_id = None
    local_categories: List[str] = []
    local_idx = 0

    last_sync_try = 0.0

    def ensure_synced():
        nonlocal local_playlist_id, local_categories, local_idx, last_sync_try

        now = time.time()
        if now - last_sync_try < 1.5:
            return
        last_sync_try = now

        # reg
        follower_register_once()

        st = follower_fetch_state()
        if not st:
            return

        pid = st.get("playlist_id")
        cats = st.get("categories") or []
        idx = int(st.get("current_idx", 0))

        if pid and pid != local_playlist_id and cats:
            local_playlist_id = pid
            local_categories = cats
            local_idx = idx
            log("FOLLOWER", f"SYNC playlist_id={pid} len={len(cats)} idx={idx}")

    # Cada vez que llega orden de índice
    def on_play_idx(idx: int):
        nonlocal local_idx

        ensure_synced()
        if not local_categories:
            return

        if idx < 0 or idx >= len(local_categories):
            return

        local_idx = idx
        cat = local_categories[idx]
        block = pick_block(cat)
        if len(block) != BLOCK_SIZE:
            log("FOLLOWER", f"Cat inválida local: {cat} -> no reproduce")
            return

        log("FOLLOWER", f"PLAY idx={idx} cat={cat} (block aleatorio local)")
        ok = mpv_load_block(ipc, block)
        if not ok:
            log("FOLLOWER", "mpv_load_block falló (ver logs MPV)")
            return

        wait_block_finished()
        follower_send_done(idx)

    threading.Thread(target=follower_listen_cmd_loop, args=(on_play_idx,), daemon=True).start()

    log("FOLLOWER", "activo. Esperando leader + sync...")

    # Loop de sync y offline básico
    while True:
        ensure_synced()

        with leader_ip_lock:
            seen = last_leader_seen

        # offline opcional: si no ve leader y ya tiene playlist, sigue avanzando
        if local_categories and (time.time() - seen) > OFFLINE_AFTER:
            # avanzar local si nadie manda comandos
            idx = local_idx
            if idx >= len(local_categories):
                idx = 0
            cat = local_categories[idx]
            block = pick_block(cat)
            if len(block) == BLOCK_SIZE:
                log("FOLLOWER", f"OFFLINE PLAY idx={idx} cat={cat}")
                mpv_load_block(ipc, block)
                wait_block_finished()
                local_idx = idx + 1
            else:
                local_idx = idx + 1

        time.sleep(0.5)

# =======================
# MAIN
# =======================

def main():
    # Audio siempre independiente
    threading.Thread(target=audio_loop, daemon=True).start()

    # mpv video + listener de eventos
    ipc = start_mpv_video()
    threading.Thread(target=mpv_event_listener, args=(IPC_SOCKET,), daemon=True).start()

    if ROLE == 0:
        log("SYS", f"LEADER | ROLE={ROLE} ORIENTATION={ORIENTATION}")
        leader_run(ipc)
    else:
        log("SYS", f"FOLLOWER | ROLE={ROLE} ORIENTATION={ORIENTATION}")
        follower_run(ipc)

if __name__ == "__main__":
    main()
