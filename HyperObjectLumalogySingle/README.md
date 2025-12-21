# Sistema Audiovisual Raspberry Pi — Autoplayer mpv (estado estable)

Este README documenta **la configuración mínima y correcta** para que el sistema funcione **hasta el punto actual**:

* Reproducción continua de video por playlist con **mpv**
* Audio drone independiente por rol
* Arranque automático controlado (sin bloquear terminal)
* Posibilidad de **modo mantenimiento** (usar `alsamixer`, editar código, etc.)
* Procedimiento de **respaldo/clonado de micro‑SD**

Probado en **Raspberry Pi 4B+** con **Raspberry Pi OS Bookworm (64‑bit, Desktop)**.

---

## 1. Sistema Operativo recomendado

* **Raspberry Pi OS Bookworm (64‑bit, Desktop)**
* Wayland o X11 (ambos funcionan con esta configuración)

> Nota: No se requiere entorno virtual de Python. Se usa Python del sistema.

---

## 2. Dependencias necesarias

Actualizar sistema e instalar mpv:

```bash
sudo apt update
sudo apt install -y mpv
```

No se requieren librerías Python externas.

---

## 3. Estructura de carpetas esperada

### Videos

```text
/home/pi/Videos/videos_hd_final/
└── CATEGORIA_1/
    ├── hor/
    ├── hor_text/
    ├── ver_rotated/
    └── ver_rotated_text/
└── CATEGORIA_2/
    ├── hor/
    ├── hor_text/
    ├── ver_rotated/
    └── ver_rotated_text/
...
```

Reglas:

* Cada categoría debe tener **al menos**:

  * 1 video en `*_text`
  * 3 videos en la carpeta sin texto
* Extensiones soportadas: `.mp4`, `.mov`, `.mkv`

### Audio

```text
/home/pi/Music/audios/
├── drone_81.WAV   # leader (ROLE=0)
├── drone_82.WAV   # follower 1
├── drone_83.WAV   # follower 2
└── drone_84.WAV   # follower 3
```

---

## 4. Script principal

Ruta del programa:

```text
/home/pi/Documents/GitHub/autoplayer/HyperObjectLumalogySingle/roleplayer.py
```

Configuraciones clave dentro del script:

```python
ROLE = 0            # 0=leader, 1..3 followers
ORIENTATION = "hor" # "hor" o "ver"
ROUNDS = 10         # Repeticiones de todas las categorías en la playlist
```

El script:

* Genera una playlist larga en `/tmp/playlist_roleX.m3u`
* Lanza **una sola instancia de mpv** por ciclo
* Regenera la playlist al terminar
* Reproduce audio en loop independiente

---

## 5. Arranque automático seguro (bashrc)

⚠️ **Nunca** lanzar directamente loops infinitos desde `.bashrc`.

Usamos un **archivo bandera** para control.

### 5.1 Bloque correcto para `~/.bashrc`

Agregar **al final** de `/home/pi/.bashrc`:

```bash
# ==============================
# AUTOPLAYER MPV (SAFE MODE)
# ==============================

if [ -f "$HOME/.autoplayer_enable" ]; then
    if ! pgrep -f "roleplayer.py" >/dev/null; then
        if [ -z "$DISPLAY" ]; then
            export DISPLAY=:0
        fi
        nohup python3 /home/pi/Documents/GitHub/autoplayer/HyperObjectLumalogySingle/roleplayer.py \
            >/tmp/autoplayer.log 2>&1 &
    fi
fi
```

Características:

* No bloquea la terminal
* No se relanza múltiples veces
* Logs en `/tmp/autoplayer.log`

---

## 6. Control del sistema

### 6.1 Activar modo exhibición (arranque automático)

```bash
touch ~/.autoplayer_enable
reboot
```

### 6.2 Desactivar (modo mantenimiento)

```bash
rm ~/.autoplayer_enable
pkill -f roleplayer.py
pkill mpv
```

Ahora puedes usar sin problema:

```bash
alsamixer
```

---

## 7. Entrar siempre a una terminal limpia (emergencia)

```bash
bash --noprofile --norc
```

Esto ignora `.bashrc`.

---

## 8. Respaldo / clonado de micro‑SD

### 8.1 Identificar la SD

Ejemplo (SD de ~32 GB):

```text
/dev/sde  29.7G  removable
```

### 8.2 Crear imagen completa

```bash
sudo dd if=/dev/sde of=~/rpi_autoplayer_32gb_backup.img bs=4M status=progress conv=fsync
```

### 8.3 Comprimir

```bash
gzip -9 ~/rpi_autoplayer_32gb_backup.img
```

Resultado típico:

* `.img` → ~29.7 GB
* `.img.gz` → ~6–10 GB

### 8.4 Restaurar (ejemplo `/dev/sdf`)

```bash
gunzip -c ~/rpi_autoplayer_32gb_backup.img.gz | sudo dd of=/dev/sdf bs=4M status=progress conv=fsync
```

---

## 9. Grabar imagen en Windows / macOS

* **Balena Etcher** (recomendado): acepta `.img.gz` directamente
* Windows: **7‑Zip** para descomprimir
* macOS: **The Unarchiver** o `gunzip`

---

## 10. Estado actual del sistema

✔ Reproducción continua estable
✔ Sin parpadeo de escritorio
✔ Audio independiente
✔ Arranque controlado
✔ Modo mantenimiento seguro
✔ Imagen clonable (golden master)

Este README refleja **el estado estable actual** del proyecto.
