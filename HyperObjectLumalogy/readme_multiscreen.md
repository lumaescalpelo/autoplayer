# Sistema Multiscreen Sincronizado (Leader + Followers)

## Instalación completa desde cero – Raspberry Pi – Arranque automático vía `.bashrc`

---

## 1. Requerimientos

- Raspberry Pi OS (Lite o Full)
- Python 3
- `mpv` instalado
- Red local compartida
- Carpeta de videos organizada por categorías
- Carpeta de audios ambientales

---

## 2. Estructura de carpetas requerida

En todas las Raspberry Pi:

```
/home/pi/
    leader.py
    follower.py
    Videos/
        videos_hd_final/
            CATEGORIA_1/
                hor/
                hor_text/
            CATEGORIA_2/
                hor/
                hor_text/
    Music/
        audios/
            audio1.mp3
            audio2.wav
            audio3.wav
```

Cada categoría requiere:
- `hor_text/` → al menos 1 video
- `hor/` → al menos 3 videos

Cada Raspberry usará un audio estable según su hostname.

---

## 3. Ubicación de los scripts

| Archivo       | Dispositivo |
|---------------|-------------|
| `leader.py`   | Solo en master001 |
| `follower.py` | En slave002, slave003, slave004 |

---

## 4. Instalación de dependencias

```
sudo apt update
sudo apt install -y mpv python3 python3-pip
```

Opcional:
```
sudo apt install -y mesa-utils
```

---

## 5. Permisos

```
chmod +x ~/leader.py
chmod +x ~/follower.py
```

---

## 6. Prueba manual

### Leader:
```
python3 ~/leader.py
```

### Follower:
```
python3 ~/follower.py
```

---

## 7. Arranque automático con `.bashrc`

### Editar archivo:
```
nano ~/.bashrc
```

### Leader (master001):
```
# --- AUTO-START LEADER ---
if [ -z "$SSH_TTY" ]; then
    nohup python3 /home/pi/leader.py > /home/pi/leader.log 2>&1 &
fi
```

### Followers (slave002, slave003, slave004):
```
# --- AUTO-START FOLLOWER ---
if [ -z "$SSH_TTY" ]; then
    nohup python3 /home/pi/follower.py > /home/pi/follower.log 2>&1 &
fi
```

### Reiniciar:
```
sudo reboot
```

Logs:
```
tail -f ~/leader.log

tail -f ~/follower.log
```

---

## 8. Comportamiento esperado

### Leader:
- Detecta categorías
- Envía PLAY una vez
- Avanza cuando recibe DONE

### Followers:
- Descubren al leader
- Se registran automáticamente
- Generan playlist basada en cache
- Reproducen audio determinístico
- Envían DONE al terminar

---

## 9. Problemas comunes

### No detecta al leader:
- Verificar red y UDP
- Confirmar que `leader.py` está corriendo

### No hay audio:
- Revisar carpeta `/home/pi/Music/audios/`

### Video entrecortado:
- Evitar rotación en mpv
- Usar máximo 1080p en Pi 3

### No arranca al iniciar:
- Revisar `.bashrc`
- Verificar permisos:
```
ls -l leader.py follower.py
```

---

## 10. Backup recomendado

```
leader.py
follower.py
README.md
Videos/videos_hd_final
Music/audios
```

---

## 11. Notas

Sistema diseñado para instalaciones museográficas: robusto, estable y autónomo. Ejecuta líder + followers sin requerir intervención manual.

