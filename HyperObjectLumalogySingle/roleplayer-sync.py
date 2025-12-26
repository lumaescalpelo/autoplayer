#!/usr/bin/env python3
# -------------------------------------------------------
# Raspberry Pi 4B+ — Autoplayer robusto (standalone + UDP sync)
# FIXES CLAVE:
# 1) IPC persistente con request_id (orden garantizado, sin “solo 1 video”)
# 2) Limpieza de mpv viejo (sin necesitar reboot para volver a correr)
# 3) mpv video nunca se cierra (no flashes)
# 4) audio independiente por ROLE (loop infinito)
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

# UDP ports (se usarán cuando conectes followers)
BCAST_PORT = 8888
REG_PORT   = 8899
CMD_PORT   = 9001
DONE_PORT  = 9100
LEADER_TIMEOUT = 6.0

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
# AUDIO
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
# MPV IPC (persistente, robusto)
# =======================

class MPVIPC:
    """
    UNA conexión persistente que:
    - envía comandos con request_id
    - lee líneas JSON (eventos + respuestas mezcladas)
    - devuelve respuesta correcta por request_id
    - cuenta end-file para saber cuándo termina el bloque
    """
    def __init__(self, sock_path: str):
        self.sock_path = sock_path
        self.sock: Optional[socket.socket] = None

        self._lock = threading.Lock()
        self._req_id = 0

        self._resp_lock = threading.Lock()
        self._resp_cv = threading.Condition(self._resp_lock)
        self._responses = {}  # request_id -> resp dict

        self._stop_evt = threading.Event()

        self.endfile_lock = threading.Lock()
        self.endfile_count = 0

    def connect(self, timeout: float = 3.0):
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        s.settimeout(timeout)
        s.connect(self.sock_path)
        s.settimeout(None)
        self.sock = s
        threading.Thread(target=self._reader_loop, daemon=True).start()

    def close(self):
        self._stop_evt.set()
        try:
            if self.sock:
                self.sock.close()
        except Exception:
            pass
        self.sock = None

    def _reader_loop(self):
        buf = b""
        while not self._stop_evt.is_set():
            try:
                chunk = self.sock.recv(4096)  # type: ignore
                if not chunk:
                    time.sleep(0.05)
                    continue
                buf += chunk
                while b"\n" in buf:
                    line, buf = buf.split(b"\n", 1)
                    if not line.strip():
                        continue
                    try:
                        msg = json.loads(line.decode("utf-8", errors="ignore"))
                    except Exception:
                        continue

                    # eventos
                    if isinstance(msg, dict) and msg.get("event") == "end-file":
                        with self.endfile_lock:
                            self.endfile_count += 1
                        continue

                    # respuestas a request_id
                    rid = msg.get("request_id") if isinstance(msg, dict) else None
                    if rid is not None:
                        with self._resp_cv:
                            self._responses[rid] = msg
                            self._resp_cv.notify_all()
            except Exception:
                time.sleep(0.05)

    def cmd(self, command: List, wait: bool = True, timeout: float = 3.0) -> dict:
        """
        Envía comando; si wait=True espera la respuesta por request_id.
        """
        if self.sock is None:
            raise RuntimeError("IPC no conectado")

        with self._lock:
            self._req_id += 1
            rid = self._req_id
            payload = {"command": command, "request_id": rid}
            data = (json.dumps(payload) + "\n").encode("utf-8")
            self.sock.sendall(data)

        if not wait:
            return {"error": "success"}

        deadline = time.time() + timeout
        with self._resp_cv:
            while time.time() < deadline:
                if rid in self._responses:
                    return self._responses.pop(rid)
                self._resp_cv.wait(timeout=0.1)

        return {"error": "timeout", "request_id": rid, "command": command}

    def reset_endfiles(self):
        with self.endfile_lock:
            self.endfile_count = 0

    def wait_block_done(self, n: int = BLOCK_SIZE):
        while True:
            with self.endfile_lock:
                if self.endfile_count >= n:
                    return
            time.sleep(0.05)

# =======================
# MPV VIDEO process mgmt
# =======================

mpv_proc: Optional[subprocess.Popen] = None

def kill_old_mpv_if_any():
    """
    Si quedó un mpv viejo con ese socket (o el socket huérfano), lo cerramos.
    """
    if not os.path.exists(IPC_SOCKET):
        return

    # Intento elegante: conectar y mandar quit
    try:
        ipc = MPVIPC(IPC_SOCKET)
        ipc.connect(timeout=1.0)
        ipc.cmd(["quit"], wait=False)
        ipc.close()
        time.sleep(0.4)
    except Exception:
        pass

    # Si el socket sigue, es huérfano o mpv no salió
    if os.path.exists(IPC_SOCKET):
        try:
            os.remove(IPC_SOCKET)
        except Exception:
            pass

def start_mpv_video() -> MPVIPC:
    global mpv_proc

    kill_old_mpv_if_any()

    rot = ORIENTATION_MAP[ORIENTATION]["rotation"]

    # Lanzar mpv persistente (sin VDPAU; hwdec correcto Pi)
    mpv_proc = subprocess.Popen([
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

    # esperar socket
    for _ in range(120):
        if os.path.exists(IPC_SOCKET):
            break
        time.sleep(0.1)
    else:
        raise RuntimeError("mpv IPC no apareció")

    ipc = MPVIPC(IPC_SOCKET)
    ipc.connect(timeout=2.0)

    # prueba simple
    resp = ipc.cmd(["get_property", "idle-active"], wait=True, timeout=2.0)
    log("MPV", f"IPC OK, idle-active={resp.get('data')}")

    return ipc

def stop_mpv_video(ipc: MPVIPC):
    global mpv_proc
    try:
        ipc.cmd(["quit"], wait=False)
    except Exception:
        pass
    try:
        ipc.close()
    except Exception:
        pass
    try:
        if mpv_proc and mpv_proc.poll() is None:
            mpv_proc.terminate()
            time.sleep(0.3)
            if mpv_proc.poll() is None:
                mpv_proc.kill()
    except Exception:
        pass
    mpv_proc = None
    try:
        if os.path.exists(IPC_SOCKET):
            os.remove(IPC_SOCKET)
    except Exception:
        pass

# =======================
# BLOCK LOAD (orden garantizado)
# =======================

def mpv_load_block(ipc: MPVIPC, block: List[Path]) -> bool:
    if len(block) != BLOCK_SIZE:
        return False

    ipc.reset_endfiles()

    # Pausa y limpia playlist; esperamos respuesta para asegurar orden
    ipc.cmd(["set_property", "pause", True], wait=True, timeout=2.0)
    ipc.cmd(["playlist-clear"], wait=True, timeout=2.0)

    # 1) replace (esperar)
    r = ipc.cmd(["loadfile", str(block[0]), "replace"], wait=True, timeout=4.0)
    if r.get("error") not in (None, "success"):
        log("MPV", f"loadfile replace error: {r}")
        return False

    # 2) append (esperar cada uno para evitar race)
    for p in block[1:]:
        r = ipc.cmd(["loadfile", str(p), "append"], wait=True, timeout=4.0)
        if r.get("error") not in (None, "success"):
            log("MPV", f"loadfile append error: {r}")
            return False

    # arrancar desde el primero
    ipc.cmd(["set_property", "playlist-pos", 0], wait=True, timeout=2.0)
    ipc.cmd(["set_property", "pause", False], wait=True, timeout=2.0)
    return True

# =======================
# STANDALONE LOOP (AUTO)
# =======================

def standalone_loop(stop_evt: threading.Event, ipc: MPVIPC, tag: str):
    playlist = build_category_playlist()
    if not playlist:
        log(tag, f"No hay categorías en {BASE_VIDEO_DIR}")
        while not stop_evt.is_set():
            time.sleep(1)

    log(tag, f"Standalone activo. Playlist categorías={len(playlist)} (ROUNDS={ROUNDS})")

    idx = 0
    while not stop_evt.is_set():
        if idx >= len(playlist):
            playlist = build_category_playlist()
            idx = 0
            log(tag, f"Rebuild playlist categorías={len(playlist)}")

        cat = playlist[idx]
        block = pick_block(cat)
        if len(block) != BLOCK_SIZE:
            idx += 1
            continue

        log(tag, f"▶ idx={idx} cat={cat}")
        ok = mpv_load_block(ipc, block)
        if not ok:
            # si algo salió mal, intentamos continuar sin quedarnos colgados
            time.sleep(0.2)
            idx += 1
            continue

        ipc.wait_block_done(BLOCK_SIZE)
        idx += 1

# =======================
# MAIN
# =======================

def main():
    stop_evt = threading.Event()

    def handle_sig(*_):
        stop_evt.set()

    signal.signal(signal.SIGINT, handle_sig)
    signal.signal(signal.SIGTERM, handle_sig)

    # audio independiente
    threading.Thread(target=audio_loop, args=(stop_evt,), daemon=True).start()

    # video mpv persistente
    ipc = start_mpv_video()

    try:
        # Por ahora: standalone siempre (leader/follower sync lo activamos en el siguiente paso)
        # Esto asegura que SIEMPRE reproduce aunque esté solo.
        tag = "LEADER" if ROLE == 0 else f"FOLLOWER{ROLE}"
        standalone_loop(stop_evt, ipc, tag)

    finally:
        stop_mpv_video(ipc)

if __name__ == "__main__":
    main()
