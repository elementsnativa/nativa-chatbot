# Integración chatbot ↔ CRM de servicio al cliente

Guía para quien mantenga este repo. El CRM (bandeja, métricas, inventario) es otra
app, **`elementsnativa/nativa-crm`** (Next.js en Vercel). Este repo es el que
**escribe** en el CRM: cada mensaje de WhatsApp, Instagram y Gmail queda como parte
de un ticket en Supabase.

```
WhatsApp / Instagram (webhooks Meta) ─┐
Gmail sac@ (polling cada 60 s) ───────┼─► este chatbot (Railway) ──► Supabase Postgres ◄── nativa-crm (Vercel)
Shopify (webhook refunds/create) ─────┘     crm.py · crm_gmail.py       tickets, messages,     lee con supabase-js
                                             crm_media.py                ticket_events…         + Realtime
                         nativa-crm ──POST /crm/send, /crm/insights──► (con header X-CRM-Secret)
```

- Producción chatbot: `https://web-production-0ef924.up.railway.app`
- Producción CRM: `https://nativa-crm-supabase.vercel.app`
- Supabase: proyecto `xulxsoiqhofvetdidzld`
- La base del chatbot (carritos, historial del bot, `bot_config`) sigue siendo el Postgres de Railway (`DATABASE_URL`).
  El CRM usa **otra** base: Supabase (`CRM_DATABASE_URL`). No se mezclan.

**Interruptor general:** si `CRM_DATABASE_URL` no está definido, todas las funciones de `crm.py`
son no-op y el chatbot se comporta como antes del CRM.

---

## Módulos

| Archivo | Qué hace |
|---|---|
| `crm.py` | Núcleo: `ingest()`, `mark_needs_human()`, clasificación con Claude, scheduler, endpoints `/crm/*` y webhook de reembolsos. |
| `crm_gmail.py` | Lee y responde `sac@nativaelements.com` con una cuenta de servicio de Google Workspace (delegación de dominio). Filtra remitentes automáticos. `import_history()` para traer historial. |
| `crm_media.py` | Descarga fotos y archivos de WhatsApp/Instagram al llegar y los sube a Supabase Storage (bucket privado `attachments`). |
| `whatsapp_routes.py` / `instagram_routes.py` | Llaman a `crm.ingest()` / `crm.mark_needs_human()` en cada mensaje (cliente, bot, agente, escalamiento). |
| `instagram_client.get_profile()` | Nombre y @usuario del cliente de Instagram (API de perfil). |

## Cómo se registra un mensaje

Todo pasa por **`crm.ingest(channel, thread_key, author, body, ...)`**, que llama a la función SQL
`crm_ingest_message`. Esa función busca o crea el ticket, guarda el mensaje y actualiza los tiempos de SLA.

| Parámetro | Valores |
|---|---|
| `channel` | `whatsapp` · `instagram` · `email` |
| `thread_key` | teléfono (WhatsApp) · PSID (Instagram) · `threadId` (Gmail) |
| `author` | `cliente` · `bot` · `agente` · `sistema` |
| `external_id` | **Siempre con prefijo**: `wa:<wamid>`, `ig:<mid>`, `gmail:<id>`. Es único: deduplica ecos y reintentos de Meta. |
| `needs_human=True` | Pasa el ticket a la cola humana (empieza a correr el SLA). WhatsApp lo usa siempre mientras `WHATSAPP_BOT_ENABLED = False`; Gmail siempre. |

Reglas que conviene respetar:

- **`ingest()` nunca lanza excepciones.** Si Supabase falla, loguea y sigue: el chatbot no debe caerse por el CRM.
- **El bot escaló o un humano tomó la conversación → `crm.mark_needs_human(channel, thread_key)`.**
  El SLA corre desde el primer mensaje del cliente que siguió sin respuesta.
- Un mensaje **saliente del bot** se registra con `author="bot"`; uno escrito por una **persona** con `author="agente"`
  (desde el CRM, desde la app de Instagram o, en coexistencia, desde la app WhatsApp Business: ecos `smb_message_echoes`).
- Si se agrega un **canal o un tipo de mensaje nuevo**, basta con llamar a `crm.ingest()` con su prefijo de `external_id`.

## Endpoints

Todos los `/crm/*` exigen el header **`X-CRM-Secret: <CRM_API_SECRET>`** (el mismo valor en Railway y Vercel).

| Método | Ruta | Uso |
|---|---|---|
| POST | `/crm/send` | `{ticket_id, body, agent_email}` → envía por el canal del ticket (WhatsApp, Instagram o Gmail), pausa el bot en esa conversación y registra el mensaje como `agente`. |
| GET | `/crm/gmail-attachment?message_id=&attachment_id=&mime=` | Devuelve un adjunto de Gmail (la web los pide al abrir el ticket). |
| POST | `/crm/insights` | `{start, end, requested_by}` (fechas `YYYY-MM-DD`) → análisis con Claude de los tickets del rango; lo guarda en la tabla `insights`. |
| POST | `/webhook/shopify/refund` | Webhook `refunds/create` (HMAC con `SHOPIFY_WEBHOOK_SECRET`) → tabla `shopify_refunds`. Se registra con `GET /setup/shopify-webhooks`. |

## Tareas en segundo plano (`start_crm_scheduler`, hilo daemon en `main.py`)

| Tarea | Cada | Qué hace |
|---|---|---|
| Gmail | 60 s | Trae correos de los últimos 2 días que aún no están (`poll_gmail`). Solo si existe `GMAIL_SERVICE_ACCOUNT_JSON`. |
| Clasificación | 120 s | Hasta 20 tickets con mensajes nuevos: Claude (`CRM_AI_MODEL`, por defecto `claude-opus-5`) devuelve motivo, responsable, solución, sentimiento, estado sugerido y resumen. **No toca los campos en `tickets.manual_fields`** (lo que SAC editó a mano manda). |
| Cierre automático | 10 min | Tickets atendidos solo por el bot → resueltos a las 48 h; resueltos → cerrados a los 7 días (`crm_autoclose()`). |
| Perfiles Instagram | 10 min | Completa nombre/@usuario de clientes que entraron sin ellos. |

El scheduler corre dentro del proceso web. **Si Railway escala a más de una réplica, las tareas se duplican**
(no se rompe nada por la deduplicación, pero gasta el doble de llamadas a Claude).

## Variables de entorno (Railway)

Solo nombres. Los valores se entregan aparte, nunca en el repo.

**Del CRM**

| Variable | Para qué |
|---|---|
| `CRM_DATABASE_URL` | Postgres de Supabase, **Session pooler** (puerto 5432; Railway no siempre soporta la conexión directa IPv6). Sin ella, el CRM queda apagado. |
| `CRM_API_SECRET` | Secreto compartido con la web del CRM (`X-CRM-Secret`). |
| `CRM_AI_MODEL` | Opcional. Modelo de Claude para clasificar e insights. |
| `GMAIL_SERVICE_ACCOUNT_JSON` | JSON completo (una línea) de la cuenta de servicio `crm-gmail@nativa-sac.iam.gserviceaccount.com`. |
| `GMAIL_SAC_ADDRESS` | Opcional. Casilla a leer; por defecto `sac@nativaelements.com`. |
| `SUPABASE_URL` / `SUPABASE_SECRET_KEY` | Para subir fotos a Storage. Sin ellas, los adjuntos de WhatsApp/Instagram quedan solo como "[imagen]". |
| `WHATSAPP_APP_AGENT` | Opcional. Cómo aparecen en el CRM las respuestas enviadas desde la app del celular (por defecto `app-whatsapp`). |

**Del chatbot (ya existían):** `DATABASE_URL`, `ANTHROPIC_API_KEY`, `WHATSAPP_TOKEN`, `WHATSAPP_PHONE_ID`,
`WHATSAPP_WABA_ID`, `WHATSAPP_VERIFY_TOKEN`, `INSTAGRAM_PAGE_TOKEN`, `INSTAGRAM_VERIFY_TOKEN`,
`SHOPIFY_STORE_URL`, `SHOPIFY_ADMIN_TOKEN`, `SHOPIFY_WEBHOOK_SECRET`, `RAILWAY_PUBLIC_URL`, `ADMIN_SECRET`,
`BOT_RESUME_CODE`, `CONTACTO_EMAIL`, `BLUEX_*`, y las de recuperación de carritos
(`RECOVERY_*`, `QUIET_*_HOUR`, `CART_TEST_COOLDOWN_SECONDS`, `SKIP_BACKLOG_HOURS`, `CLICK_TRACKING_DOMAIN`,
`STORE_PUBLIC_DOMAIN`, `STORE_TIMEZONE`).

## Base de datos del CRM

El esquema vive en el repo **`nativa-crm`**, carpeta `supabase/migrations/` (se aplican en orden en el SQL Editor de Supabase):

- `0001_crm_schema.sql`: tablas (`tickets`, `messages`, `ticket_events`, `customers`, `categories`,
  `team_members`, `crm_settings`, `shopify_refunds`, `insights`), funciones (`crm_ingest_message`,
  `crm_mark_needs_human`, `crm_autoclose`, cálculo de SLA en minutos hábiles), vista `ticket_metrics`, RLS por rol y Realtime.
- `0002_attachments.sql`: bucket privado `attachments`.
- `0003_manual_over_ai.sql`: `tickets.manual_fields` (lo editado a mano no lo pisa la IA).

Detalles útiles:

- **El chatbot se conecta como `postgres`** (sin RLS). Las funciones de escritura (`crm_ingest_message`, etc.) tienen
  `EXECUTE` revocado para `anon` y `authenticated`: solo el backend puede llamarlas.
- **Auditoría:** cada cambio de un ticket queda en `ticket_events` con su autor. Para que una escritura del backend quede
  atribuida, se hace `SELECT set_config('crm.actor', 'ia' | 'bot' | 'sistema' | '<email>', true)` en la misma transacción.
- **El SLA** se mide en minutos hábiles según `crm_settings.business_hours` (editable desde la web, Configuración) y se pausa en
  los estados `pendiente_cliente` y `pendiente_courier`.
- **Pruebas del esquema:** en `nativa-crm`, `npm run test:db` corre la migración en un Postgres embebido (PGlite) con escenarios de SLA, reapertura y permisos.

## Cosas a saber

- **WhatsApp:** el bot conversacional está apagado (`WHATSAPP_BOT_ENABLED = False`), pero **todo mensaje entrante
  se registra** antes de esa verificación. Responder fuera de la ventana de 24 h exige plantilla (el CRM lo advierte; `/crm/send` no envía plantillas).
- **Respuestas desde el celular (coexistencia):** solo llegan si en la app de Meta está suscrito el campo
  **`smb_message_echoes`** del webhook. Confirmar si SAC responde desde la app.
- **Historial de WhatsApp:** Meta no lo entrega por API. Lo que existe en el CRM empieza el 1-oct-2026.
- **Gmail:** solo cuenta como respuesta un correo de sac@ **en un hilo donde el cliente escribió primero**
  (así no se crean tickets por correos a proveedores). La lista de remitentes ignorados está en `_IGNORE_ADDRESSES`.
  El historial completo (desde nov-2025) ya está importado; `import_history()` es idempotente.
- **El debounce de respuestas** (10 s en Instagram) vive en memoria: un redeploy en ese intervalo pierde la respuesta pendiente del bot.
- **Costos de Claude:** la clasificación usa `claude-opus-5` por defecto. Para bajar costo, `CRM_AI_MODEL=claude-sonnet-5` o `claude-haiku-4-5`.

## Pendientes

1. Confirmar en Railway `SUPABASE_URL` y `SUPABASE_SECRET_KEY` (fotos de WhatsApp/Instagram) y probar con una foto real.
2. Suscribir `smb_message_echoes` si SAC responde desde la app del celular.
3. Probar `/crm/send` de punta a punta con un WhatsApp y un correo reales.
4. Rotar las credenciales que se compartieron durante la configuración (contraseña de la base de Supabase, token de Shopify, secreto OAuth de Google) y actualizar Railway/Vercel.
