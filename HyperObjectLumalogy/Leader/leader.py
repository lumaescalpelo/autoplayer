#!/usr/bin/env python3
# ---------------------------------------------
#  LEADER — Sistema robusto de reproducción
#  Sincronización museográfica multiscreen
#  Versión diseñada para producción (Luma Escalpelo)
# ---------------------------------------------

import socket
import os
import time
import random
import threading
from tempfile import NamedTemporaryFile
import subprocess
import getpass


# ---------------------------------------------
# CONFIGURACIÓN GENERAL
# ---------------------------------------------
USERNAME = getpass.getuser()

# Carpeta raíz donde viven las categorías:
# /home/pi/Videos/videos_hd_final/CATEGORIA/hor/
# /home/pi/Videos/videos_hd_final/CATEGORIA/hor_text/
BASE_VIDEO_DIR = f"/home/{USERNAME}/Videos/videos_hd_final"

# Carpeta de audios (opcional en leader)
BASE_AUDIO_DIR = f"/home/{USERNAME}/Music/audios"

VIDEO_EXTENSIONS = ('.mp4', '.mov')
AUDIO_EXTENSIONS = ('.mp3', '.wav', '.ogg')

# Frecuencia de broadcast del líder
BROADCAST_INTERVAL = 5          # segundos

# Tiempo de seguridad para evitar colisiones entre DONE y NEXT
DONE_DELAY = 2                  # segundos


# ---------------------------------------------
# VARIABLES GLOBALES
# ---------------------------------------------
followers = set()               # IPs registradas
categoria_queue = []            # Lista de categorías
current_category = None         # Categoría activa

done_flag = threading.Event()   # Señal cuando un follower termina


# ---------------------------------------------
# UTILIDADES
# ---------------------------------------------
def is_valid_video(filename):
    return filename.lower().endswith(VIDEO_EXTENSIONS)

def is_valid_audio(filename):
    return filename.lower().endswith(AUDIO_EXTENSIONS)


# ---------------------------------------------
# 1) Cargar categorías una sola vez
# ---------------------------------------------
def pick_categories():
    if not os.path.exists(BASE_VIDEO_DIR):
        print(f"❌ No existe la carpeta de videos: {BASE_VIDEO_DIR}")
        return []

    categorias = [
        d for d in os.listdir(BASE_VIDEO_DIR)
        if os.path.isdir(os.path.join(BASE_VIDEO_DIR, d))
    ]

    categorias.sort()
    print(f"📂 Categorías detectadas: {categorias}")
    return categorias


# ---------------------------------------------
# 2) Elegir videos para una categoría
# ---------------------------------------------
def pick_videos(categoria):
    """
    Devuelve un bloque de [texto, video1, video2, video3]
    """
    text_path = os.path.join(BASE_VIDEO_DIR, categoria, "hor_text")
    video_path = os.path.join(BASE_VIDEO_DIR, categoria, "hor")

    if not os.path.exists(text_path) or not os.path.exists(video_path):
        print(f"⚠️ No hay carpetas adecuadas para {categoria}")
        return []

    textos = [f for f in os.listdir(text_path) if is_valid_video(f)]
    videos = [f for f in os.listdir(video_path) if is_valid_video(f)]

    if len(textos) < 1 or len(videos) < 3:
        print(f"⚠️ No hay suficientes videos para {categoria}")
        return []

    text_file = random.choice(textos)
    chosen = random.sample(videos, 3)

    return [
        os.path.join(text_path, text_file),
        os.path.join(video_path, chosen[0]),
        os.path.join(video_path, chosen[1]),
        os.path.join(video_path, chosen[2]),
    ]


# ---------------------------------------------
# 3) Generar playlist temporal
# ---------------------------------------------
def generate_playlist(videos):
    f = NamedTemporaryFile(delete=False, mode='w', suffix=".m3u")
    for v in videos:
        f.write(v + '\n')
    f.close()
    return f.name


# ---------------------------------------------
# 4) Reproducción de video del líder (solo 1 pantalla)
# ---------------------------------------------
def play_video_sequence(playlist_path):
    subprocess.run([
        "mpv", "--fs", "--vo=gpu", "--hwdec=no",
        "--no-terminal", "--quiet",
        "--gapless-audio", "--image-display-duration=inf",
        "--no-stop-screensaver",
        "--keep-open=no", "--loop-playlist=no",
        f"--playlist={playlist_path}"
    ])
    os.remove(playlist_path)


# ---------------------------------------------
# 5) Broadcast del líder (solo LEADER_HERE)
# ---------------------------------------------
def broadcast_leader():
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)

    while True:
        msg = f"LEADER_HERE:{','.join(categoria_queue)}"
        sock.sendto(msg.encode(), ('<broadcast>', 8888))
        time.sleep(BROADCAST_INTERVAL)


# ---------------------------------------------
# 6) Recepción de followers
# ---------------------------------------------
def listen_for_followers():
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind(('', 8899))

    print("👂 Esperando followers en puerto 8899...")

    while True:
        data, addr = sock.recvfrom(1024)
        msg = data.decode()

        if msg.startswith("REGISTER:"):
            ip = addr[0]
            followers.add(ip)
            print(f"✅ Nuevo follower registrado: {ip}")

            # si ya hay categoría activa → sincronizar
            if current_category:
                send_to_followers(f"PLAY:{current_category}")


# ---------------------------------------------
# 7) Enviar comandos a followers
# ---------------------------------------------
def send_to_followers(message):
    for ip in list(followers):
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
                s.sendto(message.encode(), (ip, 9001))
            print(f"📨 Enviado a {ip}: {message}")
        except Exception as e:
            print(f"⚠️ Error enviando a {ip}: {e}")
            followers.discard(ip)


# ---------------------------------------------
# 8) Recepción de DONE
# ---------------------------------------------
def receive_done():
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind(('', 9100))

    print("🕓 Escuchando DONE en puerto 9100...")

    while True:
        data, addr = sock.recvfrom(1024)
        if data.decode() == 'done':
            print(f"✔ DONE recibido desde {addr[0]}")
            done_flag.set()


# ---------------------------------------------
# 9) Bucle maestro
# ---------------------------------------------
def play_loop():
    global current_category

    while True:
        random.shuffle(categoria_queue)

        for categoria in categoria_queue:

            current_category = categoria
            print(f"\n🎬 Nueva categoría: {categoria}")

            # Elegir bloque para la pantalla del líder
            videos = pick_videos(categoria)
            if not videos:
                continue

            playlist = generate_playlist(videos)

            # PLAY solo una vez a todos los followers
            send_to_followers(f"PLAY:{categoria}")

            # Seguridad para evitar colisiones con NEXT y DONE
            done_flag.clear()
            time.sleep(0.2)

            # El líder reproduce en paralelo
            threading.Thread(
                target=play_video_sequence,
                args=(playlist,),
                daemon=True
            ).start()

            print("⏳ Esperando DONE...")
            done_flag.wait()      # avanzamos cuando el PRIMERO termine

            # NEXT global
            print("⏭ Avanzando de categoría")
            send_to_followers("NEXT")

            time.sleep(DONE_DELAY)


# ---------------------------------------------
# 10) MAIN
# ---------------------------------------------
def main():
    global categoria_queue

    categoria_queue = pick_categories()
    if not categoria_queue:
        print("❌ No hay categorías disponibles.")
        return

    print(f"🟦 Plan de reproducción inicial: {categoria_queue}")

    # hilos independientes
    threading.Thread(target=broadcast_leader, daemon=True).start()
    threading.Thread(target=listen_for_followers, daemon=True).start()
    threading.Thread(target=receive_done, daemon=True).start()

    play_loop()


if __name__ == '__main__':
    main()
