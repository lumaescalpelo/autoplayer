# Autoplayer mpv — Leader / Followers (modo simple)

Este proyecto reproduce video y audio en **4 Raspberry Pi** (1 leader + 3 followers) usando **mpv**, manteniendo sincronía de **categorías** sin usar IPC de mpv ni mostrar el escritorio.

La filosofía es **simple y robusta**:

* mpv se abre y se cierra por bloque (no se mantiene vivo)
* la sincronía se logra **solo** con UDP broadcast (heartbeat + advance)
* cada cambio de categoría implica terminar una playlist y lanzar la siguiente

No hay sockets de mpv, no hay Lua, no hay JSON IPC.

---

## Concepto general

* Existe una **playlist maestra de categorías** (150 rondas × 9 categorías = 1350 pasos)
* El **leader** decide qué categoría se reproduce y cuándo se avanza
* Los **followers** siguen al leader si está presente
* Si el leader desaparece, los followers entran en **modo autónomo**

---

## Diferencias Leader vs Follower

### Leader

* Reproduce **4 videos por categoría**

  * 3 videos normales
  * 1 video de texto
* El video de texto aparece en **posición aleatoria**
* Al terminar los 4 videos:

  * avanza a la siguiente categoría
  * envía mensaje `ADV` por broadcast

### Follower

* Reproduce **6 videos por categoría**

  * 5 videos normales
  * 1 video de texto
* El video de texto aparece en **posición aleatoria**
* Si recibe `ADV`:

  * mata mpv
  * avanza a la siguiente categoría
* Si no hay heartbeat del leader:

  * reproduce categorías de forma autónoma

---

## Estructura de carpetas esperada

```
Videos/
└── videos_hd_final/
    ├── DEFORESTACION/
    │   ├── hor/
    │   └── hor_text/
    ├── DESARROLLO/
    │   ├── hor/
    │   └── hor_text/
    ├── FABRICAS/
    │   ├── hor/
    │   └── hor_text/
    ├── GANADERIA/
    ├── HURACAN/
    ├── MINERIA/
    ├── NATURALEZA-ANIMALES/
    ├── WASTE POLLUTION/
    └── WILDFIRE/
```

> Para orientación vertical se usan las carpetas `ver_rotated` y `ver_rotated_text`.

---

## Playlist maestra de categorías

Archivo requerido en **cada Raspberry**:

```
~/playlist_150rondas_categorias.txt
```

Formato por línea:

```
001    01    DEFORESTACION
001    02    DESARROLLO
...
```

El programa recorre este archivo de forma **circular**.

---

## Comunicación en red

### Protocolo (UDP broadcast)

Puerto:

```
54545
```

Mensajes:

* Heartbeat (cada 10 s):

```
HB role=0 step=123 ts=1700000000
```

* Avance de categoría:

```
ADV role=0 step=124 ts=1700000010
```

### Comportamiento

* Si un follower **recibe heartbeat reciente** → sigue al leader
* Si no recibe heartbeat por ~25 s → modo autónomo

---

## Reglas de reproducción

### Selección de videos normales

* Se elige un **punto inicial aleatorio**
* Se toman los videos de forma **circular**

### Video de texto

* Exactamente **uno por bloque**
* Posición aleatoria dentro del bloque

### Reglas duras

* Nunca 0 textos
* Nunca más de 1 texto

---

## Configuración del script

Editar al inicio del archivo:

```python
ROLE = 0          # 0 = leader, 1..3 = follower
ORIENTATION = "hor"
```

Rutas:

```python
BASE_VIDEO_DIR = ~/Videos/videos_hd_final
MASTER_CATEGORY_TXT = ~/playlist_150rondas_categorias.txt
```

---

## Ejecución

En cada Raspberry:

```bash
chmod +x autoplayer_net.py
./autoplayer_net.py
```

Leader:

```python
ROLE = 0
```

Followers:

```python
ROLE = 1  # o 2, o 3
```

---

## Qué hace este sistema bien

✔ Muy simple
✔ Fácil de debuggear
✔ No depende de mpv IPC
✔ No requiere sincronía exacta de tiempo
✔ Las categorías nunca se desalinean
✔ El leader siempre es más corto que los followers

---

## Qué NO intenta hacer

✘ Sincronía frame-perfect
✘ Crossfade entre categorías
✘ Persistir mpv sin reinicio
✘ Control fino de eventos internos de mpv

---

## Decisión de diseño (importante)

Se acepta conscientemente que:

* mpv se cierra y se vuelve a abrir entre categorías
* esto es preferible a complejidad, IPC y estados difíciles

Este proyecto **prioriza estabilidad escénica sobre elegancia técnica**.

---

## Estado del proyecto

✔ Arquitectura cerrada
✔ Comportamiento definido
✔ Listo para pruebas largas
✔ Apto para instalación artística

## Arranque automático con `.bashrc` + `.autoplayer_enable` (modo simple y probado)

Este proyecto **usa exactamente el mecanismo más simple posible**, sin wrappers ni scripts intermedios.

El objetivo es:

* no romper sesiones
* no lanzar múltiples instancias
* poder activar / desactivar el modo exhibición **sin editar archivos**

---

### Bloque correcto para `~/.bashrc`

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
        nohup python3 /home/pi/Documents/GitHub/autoplayer/HyperObjectLumalogySyncPlayer/roleplayer-sync.py \
            >/tmp/autoplayer.log 2>&1 &
    fi
fi
```

### Qué garantiza este bloque

* No bloquea la terminal
* No lanza múltiples instancias
* Funciona en login gráfico automático
* Evita `systemd`, `cron` y servicios difíciles de depurar
* Logs centralizados en `/tmp/autoplayer.log`

---

## Control del sistema

### Activar modo exhibición (autoarranque)

```bash
touch ~/.autoplayer_enable
reboot
```

### Desactivar (modo mantenimiento)

```bash
rm ~/.autoplayer_enable
pkill -f roleplayer.py
pkill mpv
```

---

## Decisión de diseño

Se **rechazan deliberadamente**:

* wrappers
* servicios `systemd`
* cron `@reboot`
* scripts auxiliares

Este sistema prioriza:

* legibilidad
* control manual
* recuperación rápida en sala

Si algo falla, se puede **leer, entender y apagar en 30 segundos**.

🖤 Fin.

