# Manual de Uso — StreamCoreOS

Este manual cubre cómo instalar, configurar y usar StreamCoreOS como streamer. No requiere conocimientos de programación.

---

## ¿Qué es StreamCoreOS?

StreamCoreOS es un backend personal para streamers de Twitch. Corre en tu propia máquina y te da:

- Autenticación con Twitch (OAuth)
- Seguimiento de estado del stream (online/offline)
- Chat bot con comandos personalizados y respuestas automáticas
- Sistema de puntos de lealtad para viewers
- Auto-moderación con reglas configurables
- Dashboard con estadísticas en tiempo real

Todo se controla mediante una API HTTP. Puedes conectarlo a OBS, a un panel web, o usarlo desde la terminal con `curl`.

---

## 1. Instalación y Configuración Inicial

### Requisitos previos
- Python con `uv` instalado
- Una aplicación registrada en [dev.twitch.tv](https://dev.twitch.tv/console/apps)

### Paso 1 — Crear la app en Twitch

1. Ve a [dev.twitch.tv/console/apps](https://dev.twitch.tv/console/apps) y haz clic en **Register Your Application**
2. Nombre: cualquiera (ej. `StreamCoreOS`)
3. OAuth Redirect URL: `http://localhost:8000/auth/twitch/callback`
4. Categoría: **Chat Bot** o **Other**
5. Guarda el **Client ID** y genera un **Client Secret**

### Paso 2 — Configurar el archivo `.env`

```bash
cp .env.example .env
```

Edita `.env` con los datos de tu app de Twitch:

```env
TWITCH_CLIENT_ID=tu_client_id_aqui
TWITCH_CLIENT_SECRET=tu_client_secret_aqui
TWITCH_REDIRECT_URI=http://localhost:8000/auth/twitch/callback

AUTH_SECRET_KEY=cambia-esto-por-algo-aleatorio-largo
```

Los demás valores tienen defaults razonables y no necesitas tocarlos para empezar.

### Paso 3 — Arrancar el servidor

```bash
uv run main.py
```

El servidor arranca en `http://localhost:8000`. La primera vez crea la base de datos automáticamente.

---

## 2. Autenticación con Twitch

Una sola vez (o cada vez que el token expire):

1. Abre en el navegador: `http://localhost:8000/auth/twitch`
2. Te redirige a Twitch para que autorices la aplicación
3. Twitch te regresa a `http://localhost:8000/auth/twitch/callback`
4. La sesión queda guardada en la base de datos

A partir de ahí el token se **renueva automáticamente cada 30 minutos** y se restaura cuando reinicias el servidor. No necesitas volver a autenticarte.

### Verificar que la sesión está activa

```
GET /stream/status
```

Si devuelve datos del canal, la autenticación funciona.

---

## 3. Estado del Stream

El sistema detecta automáticamente cuando tu stream empieza y termina.

### Ver estado actual

```
GET /stream/status
```

Respuesta de ejemplo:
```json
{
  "success": true,
  "data": {
    "online": true,
    "started_at": "2026-03-22T18:00:00Z",
    "title": "Jugando algo cool",
    "game": "Just Chatting"
  }
}
```

### Historial de sesiones

```
GET /stream/sessions?limit=20&offset=0
```

Devuelve tus últimas sesiones con hora de inicio, fin y duración.

---

## 4. Chat Bot

### Comandos personalizados

Los viewers pueden escribir `!comando` en el chat y el bot responde automáticamente.

#### Crear un comando

```
POST /chat/commands
Content-Type: application/json

{
  "name": "discord",
  "response": "¡Únete a nuestro Discord en discord.gg/ejemplo!"
}
```

Ahora cuando alguien escriba `!discord` en el chat, el bot responde.

#### Ver todos los comandos

```
GET /chat/commands
```

#### Modificar un comando

```
PUT /chat/commands/discord
Content-Type: application/json

{
  "response": "Discord actualizado: discord.gg/nuevo-link"
}
```

#### Eliminar un comando

```
DELETE /chat/commands/discord
```

### Respuestas automáticas

Sin configuración adicional, el bot envía mensajes automáticos cuando:

| Evento | Mensaje automático |
|--------|-------------------|
| Nuevo follow | Bienvenida al nuevo follower |
| Suscripción nueva | Agradecimiento por sub |
| Resub | Agradecimiento por continuar |
| Gift sub | Agradecimiento por el regalo |
| Raid | Bienvenida al raider |

### Ver chat en tiempo real (SSE)

```
GET /chat/stream
```

Devuelve un stream de eventos SSE con cada mensaje del chat. Útil para integraciones con overlays de OBS.

---

## 5. Sistema de Puntos de Lealtad

Los viewers ganan puntos automáticamente por interactuar:

| Acción | Puntos |
|--------|--------|
| Mensaje en chat | 5 pts (máx. 1 vez por minuto por usuario) |
| Follow | Configurado en el plugin |
| Suscripción | Configurado en el plugin |
| Bits/Cheer | Proporcional |
| Raid | Configurado en el plugin |

### Ver puntos de un viewer

```
GET /loyalty/points/{twitch_id}
```

Reemplaza `{twitch_id}` con el ID numérico de Twitch del viewer.

### Tabla de líderes

```
GET /loyalty/leaderboard?limit=10
```

Devuelve los 10 viewers con más puntos.

### Historial de transacciones de un viewer

```
GET /loyalty/history/{twitch_id}
```

### Crear un canje (reward)

```
POST /loyalty/rewards
Content-Type: application/json

{
  "name": "Elegir canción",
  "description": "El viewer elige la próxima canción",
  "cost": 500
}
```

### Ver todos los canjes disponibles

```
GET /loyalty/rewards
```

### Canjear una recompensa

```
POST /loyalty/redeem
Content-Type: application/json

{
  "twitch_id": "12345678",
  "reward_name": "Elegir canción"
}
```

El sistema verifica que el viewer tenga suficientes puntos, los descuenta y registra el canje de forma atómica.

---

## 6. Moderación

### Auto-moderación

El sistema evalúa cada mensaje del chat contra las reglas configuradas y actúa automáticamente (timeout o ban).

#### Tipos de reglas disponibles

| Tipo | Qué detecta |
|------|-------------|
| `word` | Palabras o frases específicas |
| `link` | URLs y links en el chat |
| `caps` | Mensajes con exceso de mayúsculas |
| `spam` | Mensajes repetidos o flood |

#### Crear una regla

```
POST /moderation/rules
Content-Type: application/json

{
  "type": "word_filter",
  "value": "palabrota1, palabrota2, palabrota3",
  "action": "timeout",
  "duration_s": 300
}
```

Campos:
- `type`: `word_filter`, `link_filter`, `caps_filter`, o `spam_filter`
- `value`: una palabra **o varias separadas por coma** (para `word_filter`)
- `action`: `timeout` o `ban`
- `duration_s`: segundos de timeout (solo para `timeout`)

El campo `value` acepta hasta 2000 caracteres, suficiente para listas largas de palabras.

#### Ver todas las reglas

```
GET /moderation/rules
```

#### Modificar una regla

```
PUT /moderation/rules/{id}
Content-Type: application/json

{
  "action": "ban"
}
```

#### Eliminar una regla

```
DELETE /moderation/rules/{id}
```

### Moderación manual

#### Banear a un usuario

```
POST /moderation/ban
Content-Type: application/json

{
  "platform": "twitch",
  "channel_id": "12345678",
  "user_id": "87654321",
  "reason": "Spam reiterado"
}

También se acepta `twitch_id` como alias legacy de `user_id` para Twitch.
```

#### Timeout a un usuario

```
POST /moderation/timeout
Content-Type: application/json

{
  "platform": "twitch",
  "channel_id": "12345678",
  "user_id": "87654321",
  "duration_s": 600,
  "reason": "Lenguaje inapropiado"
}

Para YouTube usar `platform: "youtube"`, `channel_id` como `liveChatId` y `user_id` como channel id del usuario.
```

#### Desbanear a un usuario

```
POST /moderation/unban
Content-Type: application/json

{
  "platform": "twitch",
  "channel_id": "12345678",
  "user_id": "87654321"
}

YouTube unban no está soportado desde `user_id`; la API requiere un `liveChatBan id`.
```

### Ver registro de moderación

```
GET /moderation/log
```

Historial de todas las acciones de moderación (automáticas y manuales). Permite filtrar por `platform`, `channel_id` y `user_id`.

---

## 7. Dashboard

### Estadísticas actuales

```
GET /dashboard/stats
```

Devuelve un resumen de todo:
- Estado del stream (online/offline, viewers, juego)
- Top viewers por puntos
- Últimas acciones de moderación

### Historial de estadísticas del canal

```
GET /dashboard/stats/history
```

El sistema captura una snapshot del canal cada 5 minutos (viewers, followers). Útil para ver el crecimiento a lo largo del tiempo.

### Alertas en tiempo real (SSE)

```
GET /dashboard/alerts
```

Stream de eventos SSE con todo lo que pasa: follows, subs, raids, mensajes de mod, etc. Ideal para conectar un overlay de OBS o un panel de control web.

---

## 8. Sistema y Salud

### Verificar que todo funciona

```
GET /ping
```

Respuesta: `{"success": true, "data": "pong"}`

### Estado del sistema

```
GET /system/status
```

Estado de todas las herramientas internas (DB, Twitch, scheduler, etc.).

### Ver eventos internos recientes

```
GET /system/events
```

Útil para depurar si algo no funciona como esperas.

### Stream de logs en tiempo real

```
GET /system/logs/stream
```

Logs en vivo del servidor via SSE.

---

## 9. Documentación Interactiva de la API

Con el servidor corriendo, abre:

```
http://localhost:8000/docs
```

Interfaz Swagger completa donde puedes explorar y probar todos los endpoints directamente desde el navegador, sin necesidad de `curl`.

---

## 10. Flujo típico de uso

```
1. uv run main.py                          # Arrancar el servidor
2. Abrir http://localhost:8000/auth/twitch  # Autenticarse (solo la primera vez)
3. El servidor monitorea el stream automáticamente
4. Configurar comandos y reglas de mod vía API
5. Conectar el dashboard SSE a tu overlay de OBS (opcional)
```

A partir de ahí todo es automático: el bot responde comandos, los viewers acumulan puntos, el automod filtra mensajes y el token se renueva solo.

---

## 11. Referencia Rápida de Endpoints

| Método | Endpoint | Descripción |
|--------|----------|-------------|
| `GET` | `/ping` | Health check |
| `GET` | `/auth/twitch` | Iniciar auth con Twitch |
| `GET` | `/stream/status` | Estado actual del stream |
| `GET` | `/stream/sessions` | Historial de sesiones |
| `GET` | `/chat/stream` | SSE — mensajes del chat |
| `GET` | `/chat/commands` | Listar comandos |
| `POST` | `/chat/commands` | Crear comando |
| `PUT` | `/chat/commands/{name}` | Actualizar comando |
| `DELETE` | `/chat/commands/{name}` | Eliminar comando |
| `GET` | `/loyalty/points/{twitch_id}` | Puntos de un viewer |
| `GET` | `/loyalty/leaderboard` | Top viewers |
| `GET` | `/loyalty/history/{twitch_id}` | Historial de puntos |
| `GET` | `/loyalty/rewards` | Listar recompensas |
| `POST` | `/loyalty/rewards` | Crear recompensa |
| `POST` | `/loyalty/redeem` | Canjear recompensa |
| `GET` | `/moderation/rules` | Listar reglas de mod |
| `POST` | `/moderation/rules` | Crear regla |
| `PUT` | `/moderation/rules/{id}` | Actualizar regla |
| `DELETE` | `/moderation/rules/{id}` | Eliminar regla |
| `POST` | `/moderation/ban` | Banear usuario |
| `POST` | `/moderation/timeout` | Timeout a usuario |
| `POST` | `/moderation/unban` | Desbanear usuario |
| `GET` | `/moderation/log` | Log de moderación |
| `GET` | `/dashboard/stats` | Estadísticas actuales |
| `GET` | `/dashboard/stats/history` | Historial de stats |
| `GET` | `/dashboard/alerts` | SSE — alertas en tiempo real |
| `GET` | `/system/status` | Estado del sistema |
| `GET` | `/system/events` | Eventos internos |
