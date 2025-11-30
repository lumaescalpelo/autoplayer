import socket
import os
import random
import threading
import time
import subprocess
import getpass
from tempfile import NamedTemporaryFile

USERNAME = getpass.getuser()
BASE_VIDEO_DIR = f"/home/{USERNAME}/Videos/videos_hd_final"
BASE_AUDIO_DIR = f"/home/{USERNAME}/Music/audios"
VIDEO_SUBFOLDERS = ["ver_rotated_text", "ver_rotated"]
VIDEO_EXTENSIONS = ('.mp4', '.mov')
AUDIO_EXTENSIONS = ('.mp3', '.wav', '.ogg')

LEADER_IP = None
CATEGORIAS = []
ultima_categoria = None
categoria_lock = threading.Lock()

def discover_leader():
    global LEADER_IP
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind(('', 8888))
    while not LEADER_IP:
        data, addr = sock.recvfrom(1024)
        if data.decode().startswith("LEADER_HERE"):
            LEADER_IP = addr[0]
            print(f" ^|^e L  der detectado en {LEADER_IP}")
            break

def register_with_leader():
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.sendto(f"REGISTER:{socket.gethostname()}".encode(), (LEADER_IP, 8899))
        print(" ^=^s  Registrado con el l  der")
    except Exception as e:
        print(f" ^}^l Registro fallido: {e}")

def listen_commands():
    global ultima_categoria
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind(('', 9001))
    print(" ^=^n  Escuchando comandos del l  der en puerto 9001 UDP...")
    while True:
        data, _ = sock.recvfrom(1024)
        msg = data.decode()
        if msg.startswith("CATEGORIAS:"):
            CATEGORIAS.clear()
            CATEGORIAS.extend(msg.split(":", 1)[1].split(","))
            print(f" ^=^s^b Categor  as disponibles: {CATEGORIAS}")
        elif msg.startswith("PLAY:"):
            categoria = msg.split(":", 1)[1]
            print(f" ^=^n  Instrucci  n PLAY recibida: {categoria}")
            with categoria_lock:
                if categoria != ultima_categoria:
                    ultima_categoria = categoria
                    threading.Thread(target=reproduce_categoria, args=(categoria,), daemon=True).start()
        elif msg == "NEXT":
            print(" ^~   ^o Recibido NEXT")

def is_valid_video(filename):
    return filename.lower().endswith(VIDEO_EXTENSIONS)

def is_valid_audio(filename):
    return filename.lower().endswith(AUDIO_EXTENSIONS)

def pick_videos(categoria):
    blocks = []
    text_path = os.path.join(BASE_VIDEO_DIR, categoria, "ver_rotated_text")
    video_path = os.path.join(BASE_VIDEO_DIR, categoria, "ver_rotated")

    if not os.path.exists(text_path) or not os.path.exists(video_path):
        return []

    textos = [f for f in os.listdir(text_path) if is_valid_video(f)]
    videos = [f for f in os.listdir(video_path) if is_valid_video(f)]

    if len(textos) >= 1 and len(videos) >= 3:
        text_file = random.choice(textos)
        video_files = random.sample(videos, 3)
        block = [os.path.join(text_path, text_file)] + [os.path.join(video_path, v) for v in video_files]
        blocks.append(block)

    return blocks

def generate_playlist(blocks):
    f = NamedTemporaryFile(delete=False, mode='w', suffix=".m3u")
    for block in blocks:
        for video in block:
            f.write(video + '\n')
    f.close()
    return f.name

def reproduce_categoria(categoria):
    videos = pick_videos(categoria)
    if not videos:
        print(" ^z   ^o No se encontraron suficientes videos para la categor  a")
        return
    playlist = generate_playlist(videos)
    print(f" ^v   ^o Reproduciendo videos: {videos}")
    subprocess.run([
        "mpv", "--fs", "--vo=gpu", "--hwdec=no", "--no-terminal", "--quiet",
        "--gapless-audio", "--image-display-duration=inf", "--no-stop-screensaver",
        "--keep-open=no", "--loop-playlist=no", "--vf=rotate=PI:bilinear=0", f"--playlist={playlist}"
    ])
    os.remove(playlist)
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.sendto(b'done', (LEADER_IP, 9100))
        print(" ^|^e Se  al DONE enviada al l  der")
    except Exception as e:
        print(f" ^z   ^o Error al enviar DONE: {e}")

def play_audio_background():
    files = [f for f in os.listdir(BASE_AUDIO_DIR) if is_valid_audio(f)]
    if not files:
        return
    file = random.choice(files)
    print(f" ^=^t^j Reproduciendo audio ambiental: {file}")
    subprocess.Popen([
        "mpv", "--no-video", "--loop=inf", "--quiet", "--no-terminal",
        os.path.join(BASE_AUDIO_DIR, file)
    ])

def main():
    threading.Thread(target=listen_commands, daemon=True).start()
    discover_leader()
    register_with_leader()
    play_audio_background()
    while True:
        time.sleep(1)

if __name__ == '__main__':
    main()

