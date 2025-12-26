#!/usr/bin/env python3
# -------------------------------------------------------
# Raspberry Pi 4B+ — Autoplayer mpv con Leader/Follower
# - Sin IPC (no mpv socket / no JSON IPC)
# - Rudimentario: UDP broadcast heartbeat + "advance"
# - Leader: por categoría reproduce 4 videos (3 normales + 1 texto)
# - Follower: por categoría reproduce 6 videos (5 normales + 1 texto)
# - Si follower pierde heartbeat: modo autónomo (sigue lista circular)
# - Si leader vuelve: follower se re-alinea al step del leader (mata mpv y salta)
# -------------------------------------------------------

import os
import re
import random
import socket
import subprocess
import time
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Tuple

# =======================
# CONFIG
# =======================

# ROLE:
# 0 = leader
# 1..3 = followers
ROLE = 0

# Orientación física de la pantalla:
# "hor" | "ver" | "inverted_hor" | "inverted_ver"
ORIENTATION = "hor"

# Directorios base
BASE_VIDEO_DIR = Path.home() / "Videos" / "videos_hd_final"
BASE_AUDIO_DIR = Path.home() / "Music" / "audios"

# Playlist maestra de categorías (150 rondas * 9 cats = 1350 steps)
# Puedes cambiarlo a donde guardes el .txt generado.
MASTER_CATEGORY_TXT = Path.home() / "playlist_150rondas_categorias.txt"

# extensiones válidas
VIDEO_EXTENSIONS = (".mp4", ".mov", ".mkv")

# mpv
MPV_BIN = "mpv"
MPV_COMMON = [
    "--fs",
    "--force-window=yes",
    "--keep-open=no",
    "--no-terminal",
    "--hwdec=auto-safe",
    "--vo=gpu",
    "--scale=bilinear",
    "--panscan=1.0",
    "--no-keepaspect-window",
    "--video-aspect-override=no",
    "--stop-screensaver=yes",
]

# Red (UDP broadcast)
BCAST_PORT = 54545
BCAST_ADDR = "255.255.255.255"
HEARTBEAT_EVERY_S = 10.0
LEADER_DEAD_AFTER_S = 25.0  # si no hay heartbeat en este tiempo → autónomo
SOCKET_TIMEOUT_S = 1.0

# Si quieres reproducibilidad, fija SEED_GLOBAL; si no, usa None
SEED_GLOBAL = None

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
        "rotation": 0,
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
# AUDIO (tu lógica)
# =======================

def pick_audio(role: int) -> Path:
    return BASE_AUDIO_DIR / {
        0: "drone_81.WAV",
        1: "drone_82.WAV",
        2: "drone_83.WAV",
        3: "drone_84.WAV",
    }.get(role, "drone_81.WAV")

def audio_loop(stop_evt: threading.Event):
    proc = None
    audio_path = str(pick_audio(ROLE))
    while not stop_evt.is_set():
        if proc is None or proc.poll() is not None:
            proc = subprocess.Popen([
                MPV_BIN,
                "--no-terminal",
                "--loop-file=inf",
                "--audio-display=no",
                audio_path
            ])
        time.sleep(1)

# =======================
# HELPERS: videos
# =======================

def is_video(p: Path) -> bool:
    return p.is_file() and p.suffix.lower() in VIDEO_EXTENSIONS

def category_dirs(cat: str) -> Tuple[Path, Path]:
    cfg = ORIENTATION_MAP[ORIENTATION]
    return (
        BASE_VIDEO_DIR / cat / cfg["text_dir"],
        BASE_VIDEO_DIR / cat / cfg["video_dir"],
    )

def list_category_assets(cat: str) -> Tuple[List[Path], List[Path]]:
    text_dir, vid_dir = category_dirs(cat)
    textos = [p for p in text_dir.iterdir() if is_video(p)] if text_dir.exists() else []
    vids   = [p for p in vid_dir.iterdir() if is_video(p)] if vid_dir.exists() else []
    return textos, vids

def circular_take(items: List[Path], n: int, start_index: int) -> List[Path]:
    """Toma n items desde start_index circularmente. Si items vacío, devuelve []"""
    if not items or n <= 0:
        return []
    out = []
    L = len(items)
    for i in range(n):
        out.append(items[(start_index + i) % L])
    return out

def build_block_playlist(
    cat: str,
    block_len: int,
    normal_count: int,
    rng: random.Random,
) -> Optional[List[Path]]:
    """
    Regla:
    - exacto 1 texto (random)
    - normales en orden desde un punto aleatorio (circular)
    - total = block_len
    """
    textos, vids = list_category_assets(cat)

    if not textos:
        return None
    if not vids:
        return None

    # escoger normales desde un punto aleatorio (circular)
    start = rng.randrange(len(vids)) if vids else 0
    normals = circular_take(vids, normal_count, start)

    # texto exacto 1
    texto = rng.choice(textos)

    block = normals + [texto]
    # reordenar para que el texto aparezca en posición aleatoria
    rng.shuffle(block)

    # asegurar longitud exacta (por si hay listas cortas y circular repite: ok)
    if len(block) != block_len:
        # fallback: ajusta (muy raro)
        block = block[:block_len]
        while len(block) < block_len:
            block.append(rng.choice(vids))
        # fuerza 1 texto: si se perdió, mete uno
        if sum(1 for p in block if p.parent.name.endswith("text") or p.parent.name.endswith("text_dir")) == 0:
            block[rng.randrange(block_len)] = texto

    # validación "exactamente 1 texto" (por carpeta)
    # heurística: texto viene del text_dir, así que basta contar si path está dentro.
    text_dir, _ = category_dirs(cat)
    text_count = sum(1 for p in block if str(p).startswith(str(text_dir)))
    if text_count != 1:
        # fuerza: deja 1 texto
        # elimina extras
        kept = []
        text_kept = False
        for p in block:
            is_text = str(p).startswith(str(text_dir))
            if is_text and not text_kept:
                kept.append(p)
                text_kept = True
            elif not is_text:
                kept.append(p)
        # si no quedó texto, mete uno
        if not text_kept:
            kept[rng.randrange(len(kept))] = texto
        block = kept[:block_len]
        while len(block) < block_len:
            block.append(rng.choice(vids))

    return block

def write_m3u(path: Path, items: List[Path]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        f.write("#EXTM3U\n")
        for p in items:
            f.write(str(p) + "\n")

# =======================
# MASTER CATEGORY PLAYLIST (circular)
# =======================

def parse_master_txt(path: Path) -> List[str]:
    """
    Espera líneas: "RRR\tPP\tCATEGORIA"
    """
    if not path.exists():
        raise FileNotFoundError(f"No existe MASTER_CATEGORY_TXT: {path}")
    cats = []
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            parts = line.split("\t")
            if len(parts) >= 3:
                cats.append(parts[2].strip())
            else:
                # fallback: última columna por espacios
                cats.append(line.split()[-1])
    return cats

# =======================
# UDP broadcast protocol
# =======================

def make_socket() -> socket.socket:
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
    s.settimeout(SOCKET_TIMEOUT_S)
    # bind para recibir
    s.bind(("", BCAST_PORT))
    return s

def send_bcast(sock: socket.socket, msg: str):
    data = msg.encode("utf-8", errors="replace")
    sock.sendto(data, (BCAST_ADDR, BCAST_PORT))

def parse_msg(raw: str) -> Tuple[str, dict]:
    """
    Formato simple:
      "HB role=0 step=123 ts=1700000000"
      "ADV role=0 step=124 ts=..."
    """
    raw = raw.strip()
    if not raw:
        return ("", {})
    parts = raw.split()
    kind = parts[0]
    kv = {}
    for token in parts[1:]:
        if "=" in token:
            k, v = token.split("=", 1)
            kv[k] = v
    return (kind, kv)

# =======================
# SYNC STATE
# =======================

@dataclass
class SyncState:
    leader_last_seen: float = 0.0
    leader_step: int = 0
    leader_alive: bool = False
    jump_to_step: Optional[int] = None  # cuando llegue ADV o re-alineación
    lock: threading.Lock = threading.Lock()

# =======================
# NETWORK threads
# =======================

def leader_broadcast_loop(stop_evt: threading.Event, sock: socket.socket, state: SyncState):
    while not stop_evt.is_set():
        with state.lock:
            step = state.leader_step
        ts = int(time.time())
        send_bcast(sock, f"HB role=0 step={step} ts={ts}")
        time.sleep(HEARTBEAT_EVERY_S)

def follower_listen_loop(stop_evt: threading.Event, sock: socket.socket, state: SyncState):
    while not stop_evt.is_set():
        try:
            data, _addr = sock.recvfrom(2048)
        except socket.timeout:
            # update leader_alive based on time
            now = time.time()
            with state.lock:
                state.leader_alive = (now - state.leader_last_seen) <= LEADER_DEAD_AFTER_S
            continue
        except Exception:
            continue

        raw = data.decode("utf-8", errors="replace")
        kind, kv = parse_msg(raw)
        if kv.get("role") != "0":
            continue

        now = time.time()
        with state.lock:
            state.leader_last_seen = now

            if "step" in kv:
                try:
                    state.leader_step = int(kv["step"])
                except ValueError:
                    pass

            state.leader_alive = True

            if kind == "ADV":
                # leader terminó su bloque y avanzó
                state.jump_to_step = state.leader_step

# =======================
# MPV runner with interrupt
# =======================

def run_mpv_playlist(
    playlist_path: Path,
    rotation: int,
    stop_evt: threading.Event,
    jump_evt: threading.Event
) -> None:
    """
    Corre mpv y permite interrumpirlo (si llega ADV) sin IPC:
    terminamos el proceso.
    """
    cmd = [MPV_BIN] + MPV_COMMON + [
        f"--playlist={str(playlist_path)}",
        "--loop-playlist=no",
        f"--video-rotate={rotation}",
    ]

    proc = subprocess.Popen(cmd)
    try:
        while True:
            if stop_evt.is_set():
                break
            if jump_evt.is_set():
                break
            ret = proc.poll()
            if ret is not None:
                return
            time.sleep(0.2)
    finally:
        if proc.poll() is None:
            try:
                proc.terminate()
                time.sleep(0.6)
            except Exception:
                pass
        if proc.poll() is None:
            try:
                proc.kill()
            except Exception:
                pass
        try:
            proc.wait(timeout=2)
        except Exception:
            pass

# =======================
# MAIN PLAY LOGIC
# =======================

def main():
    if SEED_GLOBAL is None:
        rng = random.Random()
    else:
        rng = random.Random(SEED_GLOBAL + ROLE)

    rotation = ORIENTATION_MAP[ORIENTATION]["rotation"]
    master_steps = parse_master_txt(MASTER_CATEGORY_TXT)
    if not master_steps:
        raise RuntimeError("MASTER_CATEGORY_TXT no tiene categorías.")

    stop = threading.Event()
    jump_evt = threading.Event()
    state = SyncState()

    # socket: leader y follower usan el mismo bind/recv
    sock = make_socket()

    # audio en background
    threading.Thread(target=audio_loop, args=(stop,), daemon=True).start()

    # network:
    if ROLE == 0:
        # leader elige un punto aleatorio de la lista al arrancar
        state.leader_step = rng.randrange(len(master_steps))
        # además emite heartbeat
        threading.Thread(target=leader_broadcast_loop, args=(stop, sock, state), daemon=True).start()
    else:
        threading.Thread(target=follower_listen_loop, args=(stop, sock, state), daemon=True).start()

    # playlist path por rol
    block_playlist = Path("/tmp") / f"block_role{ROLE}.m3u"

    print(f"✅ Autoplayer NET activo | ROLE={ROLE} | ORIENTATION={ORIENTATION}")
    print(f"   MASTER steps={len(master_steps)} | rotation={rotation}°")
    print(f"   MASTER file={MASTER_CATEGORY_TXT}")

    # follower state local
    local_step = rng.randrange(len(master_steps))

    try:
        while not stop.is_set():
            # determinar step objetivo
            if ROLE == 0:
                with state.lock:
                    step = state.leader_step
            else:
                now = time.time()
                with state.lock:
                    leader_alive = (now - state.leader_last_seen) <= LEADER_DEAD_AFTER_S
                    state.leader_alive = leader_alive
                    # jump_to_step puede venir de ADV o de re-sync
                    forced = state.jump_to_step
                    leader_step = state.leader_step

                if leader_alive:
                    # si leader está vivo, seguimos su step
                    if forced is not None:
                        local_step = forced
                        with state.lock:
                            state.jump_to_step = None
                    else:
                        # re-alineación suave al heartbeat (sin esperar ADV)
                        local_step = leader_step
                else:
                    # autónomo: seguimos local_step
                    pass

                step = local_step

            cat = master_steps[step % len(master_steps)]

            # construir bloque según rol
            if ROLE == 0:
                block_len = 4
                normal_count = 3
            else:
                block_len = 6
                normal_count = 5

            block = build_block_playlist(cat, block_len, normal_count, rng)
            if not block:
                # si no hay assets en esa categoría, avanzamos
                if ROLE == 0:
                    with state.lock:
                        state.leader_step = (state.leader_step + 1) % len(master_steps)
                    # y avisamos advance para que followers no se queden
                    ts = int(time.time())
                    send_bcast(sock, f"ADV role=0 step={state.leader_step} ts={ts}")
                else:
                    local_step = (local_step + 1) % len(master_steps)
                time.sleep(0.2)
                continue

            write_m3u(block_playlist, block)

            # Preparar interrupción
            jump_evt.clear()

            # En follower: si llega ADV mientras corre mpv, lo matamos y brincamos
            if ROLE != 0:
                # monitor de jump_to_step durante reproducción
                def watcher():
                    while not stop.is_set() and not jump_evt.is_set():
                        with state.lock:
                            j = state.jump_to_step
                            alive = state.leader_alive
                        if alive and j is not None:
                            jump_evt.set()
                            return
                        time.sleep(0.2)

                threading.Thread(target=watcher, daemon=True).start()

            # Reproducir bloque
            print(f"🎞️  role={ROLE} step={step} cat={cat} block={block_len} (1 texto)")
            run_mpv_playlist(block_playlist, rotation, stop, jump_evt)

            # ¿se interrumpió por ADV?
            if ROLE != 0 and jump_evt.is_set():
                with state.lock:
                    local_step = state.leader_step
                    state.jump_to_step = None
                continue

            # bloque terminado normalmente:
            if ROLE == 0:
                with state.lock:
                    state.leader_step = (state.leader_step + 1) % len(master_steps)
                    new_step = state.leader_step
                ts = int(time.time())
                send_bcast(sock, f"ADV role=0 step={new_step} ts={ts}")
            else:
                # follower avanza 1 step (autónomo o siguiendo leader, da igual)
                local_step = (local_step + 1) % len(master_steps)

    except KeyboardInterrupt:
        stop.set()
    finally:
        try:
            sock.close()
        except Exception:
            pass
        stop.set()

if __name__ == "__main__":
    main()

