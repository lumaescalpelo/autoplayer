# Sistema audiovisual Raspberry Pi (Leader / Followers)

Este documento describe **paso a paso** cómo preparar una Raspberry Pi 4B+ para ejecutar el sistema audiovisual basado en **mpv + Python**, con **audio continuo por rol** y **reproducción de video fluida en una sola instancia de mpv**, sin flashes de escritorio.

El sistema está pensado para:

* 1 **Leader** (ROLE=0)
* 3 **Followers** (ROLE=1,2,3)

Cada nodo:

* Reproduce **audio en loop infinito** (distinto por rol)
* Reproduce **video en una sola ventana mpv**
* Genera playlists largas aleatorias por categorías
* Se recupera automáticamente si mpv se cierra

---

## 1. Sistema operativo recomendado

### Opción recomendada

* **Raspberry Pi OS Bookworm (64-bit)**
* Con Desktop (para pruebas) o Lite (para instalación final tipo kiosk)

Bookworm es estable, compatible con mpv y Python 3 estándar, y evita problemas actuales de dependencias presentes en Trixie.

### Flasheo

Usa **Raspberry Pi Imager** desde otra computadora:

1. Selecciona *Raspberry Pi OS (64-bit)*
2. Configura:

   * Usuario y contraseña
   * WiFi (si aplica)
   * SSH (opcional pero recomendado)
3. Graba la microSD

---

## 2. Actualizar sistema

En la Raspberry Pi:

```bash
sudo apt update
sudo apt full-upgrade -y
sudo reboot
```

---

## 3. Paquetes necesarios

### Instalar mpv

```bash
sudo apt install -y mpv
```

No se requieren librerías Python externas.

---

## 4. Estructura de carpetas

### Videos

Los videos **deben** estar en:

```
/home/<usuario>/Videos/videos_hd_final/
```

Estructura esperada por categoría:

```
videos_hd_final/
├── categoria_1/
│   ├── hor/
│   │   ├── video1.mp4
│   │   ├── video2.mp4
│   │   └── ...
│   └── hor_text/
│       ├── texto1.mp4
│       └── ...
├── categoria_2/
│   ├── hor/
│   └── hor_text/
└── ...
```

Notas importantes:

* Cada categoría debe tener **al menos**:

  * 1 video en `hor_text`
  * 3 videos en `hor`
* Si una categoría no cumple esto, se saltará automáticamente

> Si usas video vertical, el programa espera:
>
> * `ver/`
> * `ver_text/`

---

### Audios

Los audios deben estar en:

```
/home/<usuario>/Music/audios/
```

Archivos requeridos:

```
drone_81.WAV   # Leader
drone_82.WAV   # Follower 1
drone_83.WAV   # Follower 2
drone_84.WAV   # Follower 3
```

Formato recomendado:

* WAV
* Sample rate estándar (44.1k o 48k)

---

## 5. Script principal

Guarda el programa como:

```bash
/home/<usuario>/video_system.py
```

Dale permisos de ejecución:

```bash
chmod +x ~/video_system.py
```

---

## 6. Configuración del rol

Dentro del archivo `video_system.py`, edita:

```python
ROLE = 0        # 0 = leader, 1 / 2 / 3 = followers
ORIENTATION = "hor"  # "hor" o "ver"
```

Configura **una Raspberry Pi por rol**:

| Dispositivo | ROLE | Audio        |
| ----------- | ---- | ------------ |
| Pi 1        | 0    | drone_81.WAV |
| Pi 2        | 1    | drone_82.WAV |
| Pi 3        | 2    | drone_83.WAV |
| Pi 4        | 3    | drone_84.WAV |

---

## 7. Ejecución manual

Para probar el sistema manualmente:

```bash
cd ~
python3 video_system.py
```

Deberías observar:

* Audio continuo inmediato
* Ventana mpv fullscreen
* Reproducción fluida de video
* Al terminar la playlist larga, se regenera y continúa

Para salir:

```text
Ctrl + C
```

---

## 8. Arranque automático con .bashrc

Este método **no usa systemd**, es simple y robusto para instalaciones artísticas.

### Editar .bashrc

```bash
nano ~/.bashrc
```

Agrega **al final del archivo**:

```bash
# --- Sistema audiovisual mpv ---
if [[ -z "$DISPLAY" ]]; then
    export DISPLAY=:0
fi

sleep 5

python3 /home/<usuario>/video_system.py &
```

Guarda y cierra.

### Importante

* El `sleep 5` permite que el sistema gráfico esté listo
* El `&` evita bloquear la sesión

Reinicia para probar:

```bash
sudo reboot
```

---

## 9. Recomendaciones para instalación final (kiosk)

Para evitar **completamente** que se vea el escritorio:

* Usar **Raspberry Pi OS Lite**
* Ejecutar mpv directamente desde consola (DRM/KMS)
* O usar un entorno kiosk mínimo

Esto se puede documentar en una fase posterior.

---

## 10. Diagnóstico rápido

### Ver si mpv está corriendo

```bash
ps aux | grep mpv
```

### Ver logs básicos

```bash
journalctl -b | tail
```

### Probar audio manualmente

```bash
mpv ~/Music/audios/drone_81.WAV
```

---

## 11. Resumen

* Un solo mpv de video por nodo
* Audio desacoplado, infinito, por rol
* Playlists largas, aleatorias y regenerativas
* Recuperación automática ante fallos
* Sin dependencias Python externas

Este setup está diseñado para **instalaciones largas, estables y sin interacción humana**.
