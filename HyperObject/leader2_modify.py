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

followers = set()
categoria_queue = []
done_flag = threading.Event()
current_category = None
text_round = 0  # Track which device should play text (0=leader, 1-3=followers)

# === Broadcast peri  dico del l  der con la categor  a actual ===
def broadcast_leader():
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
    while True:
        msg = f"LEADER_HERE:{','.join(categoria_queue)}"
        sock.sendto(msg.encode(), ('<broadcast>', 8888))
        if current_category:
            sock.sendto(f"PLAY:{current_category}".encode(), ('<broadcast>', 9001))
        time.sleep(2)

# === Escuchar registros de nuevos followers ===
def listen_for_followers():
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind(('', 8899))
    print(" ^=^q^b Esperando registros de followers en puerto 8899 UDP...")
    while True:
        data, addr = sock.recvfrom(1024)
        msg = data.decode()
        if msg.startswith("REGISTER:"):
            followers.add(addr[0])
            print(f" ^|^e Nuevo follower registrado desde {addr[0]}")

# === Enviar comandos a todos los followers ===
def send_to_followers(message):
    for ip in list(followers):
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
                s.sendto(message.encode(), (ip, 9001))
            print(f" ^=^s  Enviado a {ip}: {message}")
        except Exception as e:
            print(f" ^z   ^o Error al enviar a {ip}: {e}")
            followers.discard(ip)

# === Recibir notificaciones DONE ===
def receive_done():
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind(('', 9100))
    print(" ^=^u^s Esperando se  ales DONE en puerto 9100 UDP...")
    while True:
        data, addr = sock.recvfrom(1024)
        if data.decode() == 'done':
            print(f" ^|^t  ^o DONE recibido de {addr[0]}")
            done_flag.set()

# === Validadores ===
def is_valid_video(filename):
    return filename.lower().endswith(VIDEO_EXTENSIONS)

def is_valid_audio(filename):
    return filename.lower().endswith(AUDIO_EXTENSIONS)

# === Categor  as ===
def pick_categories():
    return [d for d in os.listdir(BASE_VIDEO_DIR)
            if os.path.isdir(os.path.join(BASE_VIDEO_DIR, d))]

# === Selecci  n de videos ===
def pick_videos(categoria, include_text=True):
    blocks = []
    text_path = os.path.join(BASE_VIDEO_DIR, categoria, "ver_rotated_text")
    video_path = os.path.join(BASE_VIDEO_DIR, categoria, "ver_rotated")

    if not os.path.exists(text_path) or not os.path.exists(video_path):
        return []

    textos = [f for f in os.listdir(text_path) if is_valid_video(f)]
    videos = [f for f in os.listdir(video_path) if is_valid_video(f)]

    if include_text and len(textos) >= 1 and len(videos) >= 3:
        # Play text + 3 regular videos
        text_file = random.choice(textos)
        video_files = random.sample(videos, 3)
        block = [os.path.join(text_path, text_file)] + [os.path.join(video_path, v) for v in video_files]
        blocks.append(block)
    elif not include_text and len(videos) >= 4:
        # Play only 4 regular videos (no text)
        video_files = random.sample(videos, 4)
        block = [os.path.join(video_path, v) for v in video_files]
        blocks.append(block)

    return blocks

# === Playlist temporal ===
def generate_playlist(blocks):
    f = NamedTemporaryFile(delete=False, mode='w', suffix=".m3u")
    for block in blocks:
        for video in block:
            f.write(video + '\n')
    f.close()
    return f.name

# === Reproductor principal ===
def play_video_sequence(playlist_path):
    subprocess.run([
        "mpv", "--fs", "--vo=gpu", "--hwdec=no", "--no-terminal", "--quiet",
        "--gapless-audio", "--image-display-duration=inf", "--no-stop-screensaver",
        "--keep-open=no", "--loop-playlist=no", "--vf=rotate=PI:bilinear=0", f"--playlist={playlist_path}"
    ])
    os.remove(playlist_path)

def play_loop():
    global current_category, text_round
    for categoria in categoria_queue:
        print(f" ^=^n  Reproduciendo categor  a: {categoria}")
        current_category = categoria
        
        # Implement staggered text playback
        if text_round == 0:
            # Round 0: Leader plays text + 3 regular, followers play only regular
            print(f" ^=^n  Ronda {text_round}: Leader reproduce texto + videos")
            blocks = pick_videos(categoria, include_text=True)
        else:
            # Rounds 1-3: Leader plays only regular videos, followers play text + regular
            print(f" ^=^n  Ronda {text_round}: Leader reproduce solo videos regulares")
            blocks = pick_videos(categoria, include_text=False)
            
        if not blocks:
            print(f" ^z   ^o No se encontraron videos para {categoria}")
            continue
            
        playlist = generate_playlist(blocks)
        threading.Thread(target=send_done_later, daemon=True).start()
        play_video_sequence(playlist)
        print(" ^o  Esperando DONE de cualquier follower...")
        done_flag.wait()
        send_to_followers("NEXT")
        done_flag.clear()
        
        # Advance to next round
        text_round = (text_round + 1) % 4

# === Marcar reproducci  n terminada ===
def send_done_later():
    time.sleep(2)
    done_flag.set()

# === Reproductor de audio de fondo ===
def play_audio_background():
    audio_files = [f for f in os.listdir(BASE_AUDIO_DIR) if is_valid_audio(f)]
    if audio_files:
        audio = random.choice(audio_files)
        subprocess.Popen([
            "mpv", "--no-video", "--loop=inf", "--quiet",
            "--no-terminal", os.path.join(BASE_AUDIO_DIR, audio)
        ])

# === MAIN ===
def main():
    global categoria_queue
    categoria_queue = pick_categories()
    print(f" ^=^n^~  ^o Plan de reproducci  n: {categoria_queue}")
    threading.Thread(target=broadcast_leader, daemon=True).start()
    threading.Thread(target=listen_for_followers, daemon=True).start()
    threading.Thread(target=receive_done, daemon=True).start()
    play_audio_background()
    while True:
        random.shuffle(categoria_queue)
        play_loop()

if __name__ == '__main__':
    main()