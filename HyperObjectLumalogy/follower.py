#!/usr/bin/env python3
# -------------------------------------------------------
#  FOLLOWER — Sistema robusto multiscreen para museo
#  Sincronizado con leader.py diseñado para Luma Escalpelo
# -------------------------------------------------------

import socket
import os
import time
import random
import threading
import subprocess
import getpass
from tempfile import NamedTemporaryFile


# -------------------------------------------------------
# CONFIGURACIÓN GENERAL
# -------------------------------------------------------
USERNAME = getpass.getuser()

BASE_VIDEO_DIR = f"/home/{USERNAME}/Videos/videos_hd_final"
BASE_AUDIO_DIR = f"/home/{USERNAME}/Music/audios"

VIDEO_EXTENSIONS = ('.mp4', '.mov')
AUDIO_EXTENSIONS = ('.mp3', '.wav', '.ogg')

LEADER_PORT_BROADCAST = 8888
FOLLOWER_REGISTER_PORT = 8899
FOL_PLAY_PORT = 9001
DONE_PORT = 9100

# Espera antes de enviar DONE (previene colisiones)
DONE_DELAY = 0.3

# Tiempo entre reintentos si no hay líder
LEADER_SEARCH_INTERVAL = 2


# -------------------------------------------------------
# VARIABLES
# -------------------------------------------------------
leader_ip = None
current_category = None
playing_flag = threading.Event()
categoria_cache = {}       # categ -> { "text":[], "videos":[] }
audio_thread_started = False


# -------------------------------------------------------
# UTILIDADES
# -------------------------------------------------------
def is_valid_video(name):
    return name.lower().endswith(VIDEO_EXTENSIONS)

def is_valid_audio(name):
    return name.lower().endswith(AUDIO_EXTENSIONS)


# -------------------------------------------------------
# 1) CACHEAR TODAS LAS CATEGORÍAS AL INICIAR
# -------------------------------------------------------
def build_cache():
    global categoria_cache

    print("📦 Construyendo cache de videos...")

    if not os.path.exists(BASE_VIDEO_DIR):
        print(f"❌ Carpeta no existe: {BASE_VIDEO_DIR}")
        return

    categorias = [
        d for d in os.listdir(BASE_VIDEO_DIR)
        if os.path.isdir(os.path.join(BASE_VIDEO_DIR, d))
    ]

    for categoria in categorias:

        text_dir = os.path.join(BASE_VIDEO_DIR, categoria, "hor_text")
        vid_dir  = os.path.join(BASE_VIDEO_DIR, categoria, "hor")

        textos = []
        videos = []

        if os.path.exists(text_dir):
            textos = [os.path.join(text_dir, f)
                      for f in os.listdir(text_dir)
                      if is_valid_video(f)]

        if os.path.exists(vid_dir):
            videos = [os.path.join(vid_dir, f)
                      for f in os.listdir(vid_dir)
                      if is_valid_video(f)]

        categoria_cache[categoria] = {
            "text": textos,
            "videos": videos
        }

    print("✔ Cache lista. Categorías detectadas:")
    print(list(categoria_cache.keys()))


# -------------------------------------------------------
# 2) AUDIO DETERMINÍSTICO POR DISPOSITIVO
# -------------------------------------------------------
def pick_audio():
    if not os.path.exists(BASE_AUDIO_DIR):
        print("⚠️ No existe carpeta de audios")
        return None

    audios = [
        f for f in os.listdir(BASE_AUDIO_DIR)
        if is_valid_audio(f)
    ]

    if not audios:
        print("⚠️ No hay audios válidos")
        return None

    hostname = socket.gethostname()
    index = sum(ord(c) for c in hostname) % len(audios)

    audio_file = os.path.join(BASE_AUDIO_DIR, audios[index])
    print(f"🔊 Audio asignado a {hostname}: {audio_file}")
    return audio_file


def audio_loop(path):
    while True:
        subprocess.run([
            "mpv", "--no-terminal", "--quiet",
            "--loop",
            path
        ])


def ensure_audio_running():
    global audio_thread_started
    if audio_thread_started:
        return

    audio_path = pick_audio()
    if audio_path:
        t = threading.Thread(
            target=audio_loop, args=(audio_path,), daemon=True
        )
        t.start()
        audio_thread_started = True


# -------------------------------------------------------
# 3) GENERAR PLAYLIST PARA LA CATEGORÍA
# -------------------------------------------------------
def make_playlist(categoria):
    block = categoria_cache.get(categoria, None)
    if not block:
        print(f"⚠️ Categoría {categoria} no encontrada en cache")
        return None

    textos = block["text"]
    videos = block["videos"]

    if len(textos) < 1 or len(videos) < 3:
        print(f"⚠️ Insuficientes videos/textos en {categoria}")
        return None

    chosen_text = random.choice(textos)
    chosen_vids = random.sample(videos, 3)

    playlist_items = [chosen_text] + chosen_vids

    f = NamedTemporaryFile(delete=False, mode="w", suffix=".m3u")
    for item in playlist_items:
        f.write(item + "\n")
    f.close()

    return f.name


# -------------------------------------------------------
# 4) REPRODUCCIÓN DEL BLOQUE
# -------------------------------------------------------
def play_category(categoria):
    global current_category

    if playing_flag.is_set():
        # ya está reproduciendo algo
        return

    print(f"\n🎬 Iniciando reproducción: {categoria}")
    current_category = categoria
    playing_flag.set()

    playlist_path = make_playlist(categoria)
    if not playlist_path:
        print(f"❌ No se pudo generar playlist para {categoria}")
        playing_flag.clear()
        return

    subprocess.run([
        "mpv", "--fs", "--vo=gpu", "--hwdec=no",
        "--no-terminal", "--quiet",
        "--gapless-audio", "--image-display-duration=inf",
        "--no-stop-screensaver",
        "--keep-open=no", "--loop-playlist=no",
        f"--playlist={playlist_path}"
    ])

    os.remove(playlist_path)

    time.sleep(DONE_DELAY)

    # Enviar DONE al leader
    send_done()

    print(f"✔ Finalizó categoría {categoria}")
    playing_flag.clear()


# -------------------------------------------------------
# 5) ENVIAR DONE AL LEADER
# -------------------------------------------------------
def send_done():
    global leader_ip

    if not leader_ip:
        return

    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.sendto(b"done", (leader_ip, DONE_PORT))
        print("📨 DONE enviado al leader")
    except:
        print("⚠️ No se pudo enviar DONE")


# -------------------------------------------------------
# 6) REGISTRARSE CON EL LEADER CUANDO APAREZCA
# -------------------------------------------------------
def discover_leader():
    global leader_ip

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind(("", LEADER_PORT_BROADCAST))

    print("🔍 Buscando líder en broadcast...")

    while True:
        try:
            data, addr = sock.recvfrom(1024)
            msg = data.decode()

            if msg.startswith("LEADER_HERE:"):
                if leader_ip != addr[0]:
                    leader_ip = addr[0]
                    print(f"👑 Leader detectado: {leader_ip}")
                    register_with_leader()
        except:
            pass


def register_with_leader():
    global leader_ip
    if not leader_ip:
        return

    msg = "REGISTER:hello"
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.sendto(msg.encode(), (leader_ip, FOLLOWER_REGISTER_PORT))
        print("📨 Registrado con éxito al leader")
    except:
        print("⚠️ Error al registrar con el leader")


# -------------------------------------------------------
# 7) ESCUCHAR PLAY/NEXT DEL LEADER
# -------------------------------------------------------
def listen_for_commands():
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind(("", FOL_PLAY_PORT))

    print("👂 Escuchando instrucciones del leader...")

    while True:
        data, addr = sock.recvfrom(1024)
        msg = data.decode()

        # ignorar mensajes que no provengan del leader
        if leader_ip and addr[0] != leader_ip:
            continue

        if msg.startswith("PLAY:"):
            categoria = msg.split(":", 1)[1]

            if categoria == current_category:
                # ignorar PLAY duplicado
                continue

            threading.Thread(
                target=play_category,
                args=(categoria,),
                daemon=True
            ).start()

        elif msg == "NEXT":
            # El leader ordena pasar a la siguiente categoría
            current_category = None
            playing_flag.clear()
            print("⏭ NEXT recibido")


# -------------------------------------------------------
# MAIN
# -------------------------------------------------------
def main():
    build_cache()
    ensure_audio_running()

    threading.Thread(target=discover_leader, daemon=True).start()
    threading.Thread(target=listen_for_commands, daemon=True).start()

    print("🟩 FOLLOWER listo y en espera...")

    # Bucle vacío para mantener vivo el programa
    while True:
        time.sleep(1)


if __name__ == "__main__":
    main()
