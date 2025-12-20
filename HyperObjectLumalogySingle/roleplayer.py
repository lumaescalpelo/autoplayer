#!/usr/bin/env python3
# -------------------------------------------------------
# RPi 4B+ — Leader/Follower (ROLE) — audio loop + video mpv único
# - ROLE=0: leader audio drone_81.WAV
# - ROLE=1..3: follower audio drone_82/83/84.WAV
# - Playlist larga: 10 rondas aleatorizando categorías
# - Bloque por categoría: 1 video con texto + 3 sin texto
# - mpv de video NO se cierra: usa IPC para recargar playlist al terminar
# - Watchdogs: si muere mpv audio o video, se relanza
# -------------------------------------------------------

import os
import time
import json
import random
import socket
import subprocess
import threading
from pathlib import Path

# =======================
# CONFIG PRINCIPAL
# =======================

ROLE = 0  # 0=leader, 1/2/3=followers

# "hor" o "ver"
ORIENTATION = "hor"

ROUNDS = 10                 # cuántas veces barajar todas las categorías
BLOCK_TEXT_COUNT = 1        # 1 video con texto
BLOCK_NO_TEXT_COUNT = 3     # 3 videos sin texto

BASE_VIDEO_DIR = Path.home() / "Videos" / "videos_hd_final"
BASE_AUDIO_DIR = Path.home() / "Music" / "audios"

VIDEO_EXTENSIONS = (".mp4", ".mov", ".mkv")  # por si hay mkv

# IPC sockets (uno por rol, para poder correr 4 raspis iguales)
IPC_DIR = Path("/tmp")
VIDEO_IPC = IPC_DIR / f"mpv_video_role{ROLE}.sock"

# =======================
# UTILIDADES
# =======================

def is_video_file(p: Path) -> bool:
    return p.is_file() and p.suffix.lower() in VIDEO_EXTENSIONS

def safe_mkdir(p: Path):
    p.mkdir(parents=True, exist_ok=True)

def pick_audio_by_role(role: int) -> Path:
    # leader=81, followers: 82/83/84
    mapping = {0: "drone_81.WAV", 1: "drone_82.WAV", 2: "drone_83.WAV", 3: "drone_84.WAV"}
    name = mapping.get(role)
    if not name:
        raise ValueError("ROLE inválido (usa 0..3)")
    return BASE_AUDIO_DIR / name

# =======================
# SCAN DE CATEGORÍAS
# =======================

def list_categories(base_dir: Path) -> list[str]:
    if not base_dir.is_dir():
        raise FileNotFoundError(f"No existe BASE_VIDEO_DIR: {base_dir}")
    cats = [d.name for d in base_dir.iterdir() if d.is_dir()]
    cats.sort()
    return cats

def category_dirs(cat: str, orientation: str) -> tuple[Path, Path]:
    # Mantiene compatibilidad con tu script: hor_text y hor
    # Para vertical: ver_text y ver (ajusta si tu naming real es distinto)
    if orientation not in ("hor", "ver"):
        raise ValueError("ORIENTATION debe ser 'hor' o 'ver'")
    text_dir = BASE_VIDEO_DIR / cat / f"{orientation}_text"
    vid_dir  = BASE_VIDEO_DIR / cat / orientation
    return text_dir, vid_dir

def pick_block_for_category(cat: str, orientation: str) -> list[Path]:
    text_dir, vid_dir = category_dirs(cat, orientation)

    text_candidates = [p for p in text_dir.iterdir()] if text_dir.is_dir() else []
    text_candidates = [p for p in text_candidates if is_video_file(p)]

    vid_candidates = [p for p in vid_dir.iterdir()] if vid_dir.is_dir() else []
    vid_candidates = [p for p in vid_candidates if is_video_file(p)]

    if len(text_candidates) < BLOCK_TEXT_COUNT:
        return []
    if len(vid_candidates) < BLOCK_NO_TEXT_COUNT:
        return []

    block = []
    block.extend(random.sample(text_candidates, BLOCK_TEXT_COUNT))
    block.extend(random.sample(vid_candidates, BLOCK_NO_TEXT_COUNT))
    return block

def build_long_playlist(orientation: str) -> list[Path]:
    cats = list_categories(BASE_VIDEO_DIR)
    if not cats:
        return []

    playlist: list[Path] = []
    for _ in range(ROUNDS):
        round_cats = cats[:]
        random.shuffle(round_cats)

        for cat in round_cats:
            block = pick_block_for_category(cat, orientation)
            # Si una categoría está “incompleta”, la saltamos en esta ronda.
            if block:
                playlist.extend(block)

    return playlist

def write_m3u(paths: list[Path], out_path: Path):
    # mpv tolera rutas con espacios si van línea por línea
    with out_path.open("w", encoding="utf-8") as f:
        for p in paths:
            f.write(str(p) + "\n")

# =======================
# MPV IPC (JSON)
# =======================

class MPVIPC:
    def __init__(self, sock_path: Path):
        self.sock_path = sock_path

    def _send(self, obj: dict) -> dict | None:
        # mpv IPC: una línea JSON por comando, respuesta por línea
        import socket as pysock
        msg = (json.dumps(obj) + "\n").encode("utf-8")

        try:
            with pysock.socket(pysock.AF_UNIX, pysock.SOCK_STREAM) as s:
                s.settimeout(1.0)
                s.connect(str(self.sock_path))
                s.sendall(msg)
                data = s.recv(65536)
            if not data:
                return None
            # mpv puede mandar múltiples líneas; tomamos la primera JSON válida
            lines = data.splitlines()
            for ln in lines:
                try:
                    return json.loads(ln.decode("utf-8", errors="ignore"))
                except Exception:
                    continue
            return None
        except Exception:
            return None

    def get_property(self, name: str):
        r = self._send({"command": ["get_property", name]})
        if r and r.get("error") == "success":
            return r.get("data")
        return None

    def loadlist_replace(self, m3u_path: Path):
        # "loadlist <path> replace"
        return self._send({"command": ["loadlist", str(m3u_path), "replace"]})

# =======================
# PROCESOS: AUDIO Y VIDEO
# =======================

def launch_audio_mpv(audio_path: Path) -> subprocess.Popen:
    # Audio infinito, sin video, silencioso
    cmd = [
        "mpv",
        "--no-terminal", "--quiet",
        "--loop-file=inf",
        "--audio-display=no",
        str(audio_path),
    ]
    return subprocess.Popen(cmd)

def launch_video_mpv(ipc_path: Path) -> subprocess.Popen:
    # Video fullscreen, ventana forzada, con IPC para recargar playlists
    # Ajustes “seguros” para RPi: hwdec auto (si hay accel la usa),
    # y evitamos cosas pesadas. 1080p debería ir bien si el decode está ok.
    if ipc_path.exists():
        try:
            ipc_path.unlink()
        except Exception:
            pass

    cmd = [
        "mpv",
        "--no-terminal", "--quiet",
        "--fs",
        "--force-window=yes",
        "--idle=yes",
        "--keep-open=no",
        "--input-ipc-server=" + str(ipc_path),

        # Rendimiento / fluidez:
        "--hwdec=auto-safe",
        "--vo=gpu",
        "--profile=fast",
        "--cache=yes",
        "--cache-secs=10",
        "--vd-lavc-threads=2",
        "--framedrop=vo",
        "--video-sync=display-resample",
        "--interpolation=no",

        # Evitar screensaver/DPMS si aplica:
        "--stop-screensaver=yes",
    ]
    return subprocess.Popen(cmd)

# =======================
# WATCHDOGS
# =======================

def watchdog_audio(role: int, stop_evt: threading.Event):
    audio_path = pick_audio_by_role(role)
    if not audio_path.is_file():
        raise FileNotFoundError(f"No existe el audio requerido: {audio_path}")

    proc = None
    while not stop_evt.is_set():
        if proc is None or proc.poll() is not None:
            proc = launch_audio_mpv(audio_path)
        time.sleep(1.0)

def playlist_manager_video(role: int, orientation: str, stop_evt: threading.Event):
    # 1) levanta mpv video una vez
    proc = launch_video_mpv(VIDEO_IPC)
    ipc = MPVIPC(VIDEO_IPC)

    # 2) carga primera playlist
    safe_mkdir(Path("/tmp"))
    m3u_path = Path(f"/tmp/playlist_role{role}.m3u")

    def rebuild_and_load():
        paths = build_long_playlist(orientation)
        if not paths:
            print("⚠️ Playlist vacía (revisa carpetas/estructura). Reintentando…")
            return False
        write_m3u(paths, m3u_path)
        r = ipc.loadlist_replace(m3u_path)
        return bool(r and r.get("error") == "success")

    # espera breve a que mpv cree el socket IPC
    t0 = time.time()
    while not stop_evt.is_set() and (not VIDEO_IPC.exists()):
        if time.time() - t0 > 5:
            break
        time.sleep(0.1)

    ok = rebuild_and_load()
    if not ok:
        print("⚠️ No se pudo cargar la playlist al inicio. Se reintentará.")

    # 3) loop: cuando mpv está idle (playlist terminó), regenerar y recargar
    idle_since = None

    while not stop_evt.is_set():
        # si mpv murió, lo relanzamos (y recargamos playlist)
        if proc.poll() is not None:
            proc = launch_video_mpv(VIDEO_IPC)
            ipc = MPVIPC(VIDEO_IPC)
            time.sleep(0.5)
            rebuild_and_load()
            idle_since = None
            time.sleep(1.0)
            continue

        idle = ipc.get_property("idle-active")
        # idle-active True cuando mpv está en idle sin archivo
        if idle is True:
            if idle_since is None:
                idle_since = time.time()
            # dale un margen pequeño para evitar falsa detección
            if time.time() - idle_since > 0.8:
                # regenerar playlist (nueva aleatorización) y reemplazar
                if rebuild_and_load():
                    idle_since = None
        else:
            idle_since = None

        time.sleep(0.5)

# =======================
# MAIN
# =======================

def main():
    stop_evt = threading.Event()

    # sanity checks
    if not BASE_VIDEO_DIR.is_dir():
        raise FileNotFoundError(f"No existe: {BASE_VIDEO_DIR}")
    if not BASE_AUDIO_DIR.is_dir():
        raise FileNotFoundError(f"No existe: {BASE_AUDIO_DIR}")

    # threads
    ta = threading.Thread(target=watchdog_audio, args=(ROLE, stop_evt), daemon=True)
    tv = threading.Thread(target=playlist_manager_video, args=(ROLE, ORIENTATION, stop_evt), daemon=True)

    ta.start()
    tv.start()

    print(f"✅ Running ROLE={ROLE} ORIENTATION={ORIENTATION} (audio loop + video mpv IPC). Ctrl+C para salir.")
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        stop_evt.set()
        time.sleep(0.5)

if __name__ == "__main__":
    main()
