#!/usr/bin/env python3
# -------------------------------------------------------
# FOLLOWER — robusto + fallback offline
# - Audio determinístico por hostname (loop infinito)
# - Cache de videos al inicio (sin I/O pesado en PLAY)
# - Si no hay leader: reproduce categorías random (offline)
# - Si reaparece leader: vuelve a modo sync automáticamente
# -------------------------------------------------------

import os
import time
import random
import socket
import threading
import subprocess
import getpass
from tempfile import NamedTemporaryFile

USERNAME = getpass.getuser()

BASE_VIDEO_DIR = f"/home/{USERNAME}/Videos/videos_hd_final"
BASE_AUDIO_DIR = f"/home/{USERNAME}/Music/audios"

VIDEO_EXTENSIONS = (".mp4", ".mov")
AUDIO_EXTENSIONS = (".mp3", ".wav", ".ogg")

BCAST_PORT = 8888
REG_PORT = 8899
CMD_PORT = 9001
DONE_PORT = 9100

# Si pasan X segundos sin ver al leader => modo offline
OFFLINE_AFTER = 15          # segundos
# Pausa entre categorías en offline (suave para CPU)
OFFLINE_GAP = 0.8           # segundos
# Delay antes de DONE (evita colisiones en red)
DONE_DELAY = 0.3            # segundos

leader_ip = None
leader_lock = threading.Lock()

current_category = None
category_lock = threading.Lock()

playing_flag = threading.Event()      # asegura 1 mpv a la vez

mode_lock = threading.Lock()
mode = "SYNC"  # "SYNC" o "OFFLINE"

cache = {}  # categoria -> {"text": [...], "videos": [...]}

audio_started = False


def is_valid_video(n: str) -> bool:
    return n.lower().endswith(VIDEO_EXTENSIONS)

def is_valid_audio(n: str) -> bool:
    return n.lower().endswith(AUDIO_EXTENSIONS)


# -------------------------------------------------------
# 1) Cachear toda la biblioteca al inicio
# -------------------------------------------------------
def build_cache():
    global cache
    if not os.path.isdir(BASE_VIDEO_DIR):
        print(f"❌ No existe BASE_VIDEO_DIR: {BASE_VIDEO_DIR}")
        return

    cats = [d for d in os.listdir(BASE_VIDEO_DIR)
            if os.path.isdir(os.path.join(BASE_VIDEO_DIR, d))]
    cats.sort()

    tmp = {}
    for cat in cats:
        text_dir = os.path.join(BASE_VIDEO_DIR, cat, "hor_text")
        vid_dir = os.path.join(BASE_VIDEO_DIR, cat, "hor")

        textos = []
        vids = []

        if os.path.isdir(text_dir):
            textos = [os.path.join(text_dir, f)
                      for f in os.listdir(text_dir)
                      if is_valid_video(f)]

        if os.path.isdir(vid_dir):
            vids = [os.path.join(vid_dir, f)
                    for f in os.listdir(vid_dir)
                    if is_valid_video(f)]

        tmp[cat] = {"text": textos, "videos": vids}

    cache = tmp
    print(f"✔ Cache lista. Categorías: {list(cache.keys())}")


# -------------------------------------------------------
# 2) Audio determinístico por hostname (canal estable)
# -------------------------------------------------------
def pick_audio_deterministic():
    if not os.path.isdir(BASE_AUDIO_DIR):
        print(f"⚠️ No existe carpeta de audio: {BASE_AUDIO_DIR}")
        return None

    audios = [f for f in os.listdir(BASE_AUDIO_DIR) if is_valid_audio(f)]
    if not audios:
        print("⚠️ No hay audios válidos")
        return None

    audios.sort()
    host = socket.gethostname()
    idx = sum(ord(c) for c in host) % len(audios)
    path = os.path.join(BASE_AUDIO_DIR, audios[idx])
    print(f"🔊 Audio asignado a {host}: {path}")
    return path


def audio_loop(path: str):
    while True:
        # loop infinito del archivo asignado
        subprocess.run(["mpv", "--no-terminal", "--quiet", "--loop", path])


def ensure_audio():
    global audio_started
    if audio_started:
        return

    p = pick_audio_deterministic()
    if p:
        threading.Thread(target=audio_loop, args=(p,), daemon=True).start()
        audio_started = True


# -------------------------------------------------------
# 3) Playlist por categoría (texto + 3 videos)
# -------------------------------------------------------
def make_playlist(cat: str):
    block = cache.get(cat)
    if not block:
        return None

    textos = block["text"]
    vids = block["videos"]

    if len(textos) < 1 or len(vids) < 3:
        return None

    items = [random.choice(textos)] + random.sample(vids, 3)

    f = NamedTemporaryFile(delete=False, mode="w", suffix=".m3u")
    for it in items:
        f.write(it + "\n")
    f.close()
    return f.name


def mpv_play_playlist(path: str):
    subprocess.run([
        "mpv",
        "--fs", "--vo=gpu", "--hwdec=no",
        "--no-terminal", "--quiet",
        "--gapless-audio", "--image-display-duration=inf",
        "--no-stop-screensaver",
        "--keep-open=no", "--loop-playlist=no",
        f"--playlist={path}"
    ])
    try:
        os.remove(path)
    except FileNotFoundError:
        pass


# -------------------------------------------------------
# 4) DONE hacia el leader
# -------------------------------------------------------
def send_done():
    with leader_lock:
        lip = leader_ip
    if not lip:
        return

    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.sendto(b"done", (lip, DONE_PORT))
        print("📨 DONE enviado")
    except Exception:
        print("⚠️ No se pudo enviar DONE")


# -------------------------------------------------------
# 5) Reproducción de categoría (1 a la vez)
# -------------------------------------------------------
def play_category(cat: str, report_done: bool):
    # asegura 1 reproducción a la vez
    if playing_flag.is_set():
        return
    playing_flag.set()

    global current_category
    with category_lock:
        current_category = cat

    pl = make_playlist(cat)
    if not pl:
        print(f"⚠️ Sin material suficiente para: {cat}")
        playing_flag.clear()
        return

    print(f"\n🎬 Reproduciendo: {cat}")
    mpv_play_playlist(pl)

    if report_done:
        time.sleep(DONE_DELAY)
        send_done()

    playing_flag.clear()


# -------------------------------------------------------
# 6) Registro con leader
# -------------------------------------------------------
def register_with_leader():
    with leader_lock:
        lip = leader_ip
    if not lip:
        return

    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.sendto(f"REGISTER:{socket.gethostname()}".encode(), (lip, REG_PORT))
        print("📡 Registrado con el leader")
    except Exception:
        print("⚠️ No se pudo registrar con el leader")


# -------------------------------------------------------
# 7) Descubrir leader + watchdog OFFLINE
# -------------------------------------------------------
def discover_leader_loop():
    global leader_ip, mode

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind(("", BCAST_PORT))

    last_seen = 0.0

    while True:
        data, addr = sock.recvfrom(4096)
        msg = data.decode(errors="ignore")

        if msg.startswith("LEADER_HERE:"):
            last_seen = time.time()

            with leader_lock:
                changed = (leader_ip != addr[0])
                leader_ip = addr[0]

            if changed:
                print(f"👑 Leader detectado: {addr[0]}")
                register_with_leader()

            # si estábamos offline y vuelve leader => volvemos SYNC
            with mode_lock:
                if mode != "SYNC":
                    mode = "SYNC"
                    print("🟦 Volviendo a modo SYNC (leader disponible)")

        # watchdog offline
        if time.time() - last_seen > OFFLINE_AFTER:
            with mode_lock:
                if mode != "OFFLINE":
                    mode = "OFFLINE"
                    with leader_lock:
                        leader_ip = None
                    print("🟧 Modo OFFLINE: no se detecta leader")


# -------------------------------------------------------
# 8) Escuchar comandos PLAY / NEXT
# -------------------------------------------------------
def listen_commands_loop():
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind(("", CMD_PORT))
    print("👂 Escuchando comandos del leader...")

    while True:
        data, addr = sock.recvfrom(4096)
        msg = data.decode(errors="ignore").strip()

        # solo aceptar comandos si estamos en SYNC
        with mode_lock:
            if mode != "SYNC":
                continue

        with leader_lock:
            lip = leader_ip
        if lip and addr[0] != lip:
            continue

        if msg.startswith("PLAY:"):
            cat = msg.split(":", 1)[1]

            with category_lock:
                same = (cat == current_category)

            if same:
                continue  # debounce

            threading.Thread(target=play_category, args=(cat, True), daemon=True).start()

        elif msg == "NEXT":
            print("⏭ NEXT recibido")
            # No reseteamos flags: el siguiente PLAY define la nueva categoría


# -------------------------------------------------------
# 9) Modo offline: reproducir random local sin leader
# -------------------------------------------------------
def offline_player_loop():
    while True:
        with mode_lock:
            m = mode

        if m != "OFFLINE":
            time.sleep(0.5)
            continue

        cats = list(cache.keys())
        if not cats:
            time.sleep(1)
            continue

        cat = random.choice(cats)
        # offline: no manda DONE
        play_category(cat, report_done=False)
        time.sleep(OFFLINE_GAP)


# -------------------------------------------------------
# MAIN
# -------------------------------------------------------
def main():
    build_cache()
    ensure_audio()

    threading.Thread(target=discover_leader_loop, daemon=True).start()
    threading.Thread(target=listen_commands_loop, daemon=True).start()
    threading.Thread(target=offline_player_loop, daemon=True).start()

    print("🟩 FOLLOWER listo (SYNC/OFFLINE automático).")
    while True:
        time.sleep(1)


if __name__ == "__main__":
    main()
