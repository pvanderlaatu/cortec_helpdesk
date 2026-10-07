**Español** | [English](README.en.md)

# CORTEC Helpdesk

Personalizaciones de Frappe Helpdesk para Corporación de Tecnología CORTEC S.R.L.

## Licencia

AGPL-3.0-or-later — Ver archivo [LICENSE](LICENSE).

## Funcionalidades

1. **Control de visibilidad por agente** — cada agente HD solo ve y accede
   a los tickets que tiene asignados.
2. **Asignación automática de tickets** — al crearse un ticket, el sistema
   resuelve el agente responsable siguiendo la cadena:
   `email remitente → Contacto → Cliente → account_manager`
3. **Email routing por doctype** — fuerza que cada email saliente use la
   cuenta de correo correcta según el módulo de origen.
4. **Avisos por Telegram** — avisa con sonido en el móvil del agente
   asignado cuando entra un WhatsApp o un correo de un cliente (opcional).
5. **Alertas en el navegador** — sonido y notificación del navegador en
   /crm y /helpdesk con los mismos eventos (opcional).
6. **Avisos por Raven** — mensaje directo de un bot de Raven, sin salir
   del servidor (opcional).
7. **Registro de consentimientos** — cada consentimiento de privacidad
   o de publicidad queda como un registro inmutable con fecha, canal,
   IP, URL y el texto aceptado, al estilo de los «Acuerdos» de Bitrix24
   (Ley 8968).
8. **Solicitudes de supresión y revocación** — expediente con los plazos
   del Decreto 37554-JP, búsqueda de todo lo que Frappe guarda del
   titular y anonimización irreversible.

## Estructura

```
cortec_helpdesk/
├── LICENSE
├── setup.py
├── requirements.txt
├── README.md                   # español
├── README.en.md                # inglés
└── cortec_helpdesk/
    ├── __init__.py
    ├── hooks.py
    ├── api.py                   # Endpoints del Worker leads-intake
    ├── consent_log.py           # Registro de consentimientos
    ├── privacy.py               # Solicitudes de supresión (Ley 8968)
    ├── patches/                 # Migración de los consentimientos antiguos
    ├── public/js/
    │   └── consent_record.js    # Botón «Registrar consentimiento»
    └── overrides/
        ├── __init__.py
        ├── hd_ticket.py         # Permisos y asignación automática
        ├── communication.py     # Email routing por doctype
        ├── alert_utils.py       # Helpers comunes de avisos
        ├── alerts.py            # Despachador: resuelve el evento y reparte
        ├── telegram.py          # Canal Telegram
        ├── raven.py             # Canal Raven
        ├── whatsapp_message.py  # Adjuntos: subida a Meta
        ├── browser_alerts.py    # Alertas audibles en /crm y /helpdesk
        ├── consent.py           # Campos de consentimiento del Lead/Contact
        └── email_group_member.py  # Altas y bajas de la lista promocional
```

## Instalación

```bash
bench get-app cortec_helpdesk https://github.com/pvanderlaatu/cortec_helpdesk
bench --site sitio.dominio.com install-app cortec_helpdesk
bench --site sitio.dominio.com migrate
bench restart
```

## Configuración requerida

### 1. Cuentas de correo

| Cuenta                  | Default Outgoing | Default Incoming | Saliente | Entrante |
| ----------------------- | :--------------: | :--------------: | :------: | :------: |
| no-responda@dominio.com |        ✅        |        ❌        |    ✅    |    ❌    |
| correo1@dominio.com     |        ❌        |        ✅        |    ✅    |    ✅    |
| correo2@dominio.com     |        ❌        |        ❌        |    ✅    |    ✅    |

Configuración adicional en **correo1@** y **correo2@** (pestaña Saliente):

- ✅ Utilice siempre esta dirección como dirección de remitente
- ✅ Utilizar siempre este nombre como nombre de remitente

Configuración en **correo1@** (pestaña Entrante):

- Opción de Sincronizar: NO VISTO
- Carpeta IMAP: solo INBOX → Append to: HD Ticket
- No vincular INBOX.Sent
- ✅ Habilitar la vinculación automática en documentos

### 2. Roles

Frappe Helpdesk usa los roles **Agent** (agente) y **Agent Manager**
(asigna y administra). Son los que reconoce `helpdesk.utils.is_agent` /
`is_agent_manager`: sin uno de ellos, el usuario entra al portal de
clientes, no a la interfaz de agentes.

| Rol            | Propósito                     | Visibilidad            |
| -------------- | ----------------------------- | ---------------------- |
| System Manager | Administrador                 | Todos los tickets      |
| Agent Manager  | Supervisor / persona asignadora | Todos los tickets    |
| HD Manager     | Supervisor helpdesk           | Todos los tickets      |
| Agent          | Agente de soporte             | Solo tickets asignados |

Los nombres antiguos **HD Agent** y **HD Agent Lead** se siguen
reconociendo, por si un sitio los creó a mano, pero no son roles
estándar de Helpdesk.

Los avisos de asignación y de tickets sin asignar se envían a los
usuarios con rol **Agent Manager** o **HD Agent Lead**.

Al agente hay que crearlo además desde la administración de Helpdesk
(crea el registro **HD Agent** y asigna el rol "Agent"). Si también
atiende WhatsApp en /crm, necesita el rol **Sales User**.

### 3. Account Manager en cada Cliente

```
ERPNext → Ventas → Clientes → [Cliente]
  → Más información → Account Manager → [email del agente]
```

### 4. Contactos vinculados a Clientes

```
ERPNext → CRM → Contactos → [Contacto]
  → Vincular con → Customer → [nombre del cliente]
```

### 5. Scheduler

```
Configuración del Sistema
  → Run Jobs only Daily if Inactive For (Days) → 365
```

### 6. Adjuntos de WhatsApp

`frappe_whatsapp` no sube los archivos a Meta: le manda un **enlace** y le
pide que lo descargue. El CRM sube los adjuntos como **privados**
(`/private/files/…`), que exigen sesión iniciada, así que Meta recibe un 403
y el mensaje queda en `failed`. Pasa con imágenes, documentos, videos y audios.

Esta app lo corrige con una subclase de WhatsApp Message
(`overrides/whatsapp_message.py`, registrada con `override_doctype_class`):

- **Sube el archivo al endpoint `/media` de Meta** y envía el mensaje con el
  `media_id`. El archivo **sigue siendo privado**: se lee con `get_content()`
  y nunca se expone por HTTP.
- **Documentos:** se envía también el `filename`, el nombre con el que el
  cliente ve y guarda el archivo.
- **No manda la ruta como pie de foto.** El CRM guarda la ruta del archivo en
  el campo `message` y `frappe_whatsapp` la enviaba como `caption`.
- **Nunca deja `message` en NULL.** `WhatsAppArea.vue` del CRM hace
  `whatsapp.message.startsWith('/files/')` sin comprobar nulos: un solo
  mensaje sin texto deja **toda la conversación en blanco**.

Se desactiva en `CORTEC Helpdesk Settings` → **Adjuntos de WhatsApp**.
Apagado, se vuelve al comportamiento original de `frappe_whatsapp`.

**Si la subida falla**, el mensaje queda en `Failed` y el motivo en Error Log
("CORTEC WhatsApp: …"). Nunca se publica un archivo de forma automática.

Límites de Meta: imagen 5 MB, video 16 MB, audio 16 MB, documento 100 MB.

Para reparar conversaciones que ya se quedaron en blanco:

```python
from cortec_helpdesk.overrides.whatsapp_message import fix_null_messages
fix_null_messages()
```

### 7. Avisos por Telegram (opcional)

Frappe CRM y Helpdesk solo avisan en pantalla. Con esta opción, el agente
asignado recibe un mensaje de un bot de Telegram (con sonido, aunque el
móvil esté bloqueado) cuando entra:

- un **WhatsApp** de un cliente en un CRM Lead / CRM Deal, o
- un **correo** recibido en un CRM Lead, CRM Deal o HD Ticket.

El aviso solo incluye el documento, el nombre del cliente y un enlace;
**nunca el texto del mensaje ni el asunto**. Los correos enviados por los
propios agentes no generan aviso.

**Configuración**

1. En Telegram, hablar con **@BotFather** → `/newbot` y copiar el token.
2. `CORTEC Helpdesk Settings` → **Avisos por Telegram**:
   - ✅ Habilitar avisos por Telegram y pegar el token.
   - Elegir qué avisar: WhatsApp y/o correos.
   - *Agrupar avisos (minutos)*: varios mensajes seguidos del mismo
     cliente generan un solo aviso por agente y documento (0 = todos).
   - Guardar.
3. Cada agente abre el bot en Telegram y pulsa **/start**.
4. Dentro de las 24 h siguientes: **Telegram → Detectar chats**, elegir el
   chat y el agente → Agregar. En la tabla se puede desactivar WhatsApp o
   correo por agente.
5. **Telegram → Enviar prueba** para confirmar que llega a cada móvil.

El sitio debe tener `host_name` en `site_config.json` para que los
enlaces del aviso apunten al dominio correcto, y los workers de la cola
`short` deben estar activos.

**En el móvil del agente**

- Poner un tono propio al chat del bot (Telegram → chat → Notificaciones →
  Sonido) y no silenciarlo.
- Android: marcar el chat como *conversación prioritaria* si debe sonar con
  No molestar activo.
- iOS: con el interruptor de silencio solo vibra; permitir Telegram en los
  modos de Concentración que use.
- Xiaomi/Redmi/POCO, Oppo/Realme/OnePlus, Vivo, Honor, Huawei: activar el
  inicio automático de Telegram, batería "Sin restricciones" y bloquear la
  app en recientes (ver <https://dontkillmyapp.com>).
- Si el agente tiene Telegram abierto en el ordenador, Telegram puede no
  avisar en el móvil mientras tanto.

### 8. Alertas en el navegador (opcional)

Mientras el agente tiene **/crm** o **/helpdesk** abierto (aunque la
pestaña esté en segundo plano), suena un tono cuando entra un WhatsApp o un
correo de un cliente en un documento asignado a él. Opcionalmente muestra
también una notificación del navegador; al hacer clic se abre el documento.
Tono distinto para WhatsApp y para correo.

Funciona con los mismos eventos que Telegram, y ambos canales son
independientes: se pueden usar juntos (navegador en el escritorio,
Telegram en el móvil).

**Configuración**

1. `CORTEC Helpdesk Settings` → **Alertas en el navegador** → ✅ Habilitar.
   Elegir WhatsApp y/o correos y el intervalo de consulta (10 s por
   defecto, mínimo 5). Guardar.
2. `bench build --app cortec_helpdesk` (publica el script en `/assets`).
3. Cada agente recarga /crm o /helpdesk y pulsa una vez el botón **🔕**
   (esquina inferior derecha) → pasa a 🔔 y el navegador pide permiso de
   notificaciones. El mismo botón silencia o reactiva el sonido.

**Limitaciones**

- Los navegadores exigen una interacción antes de reproducir sonido: tras
  recargar la página basta un clic en cualquier parte.
- Si la pestaña lleva más de 5 minutos oculta, Chrome limita las consultas a
  una por minuto: el aviso puede tardar hasta 1 minuto.
- Con /crm y /helpdesk abiertos a la vez suena una sola vez.
- En el móvil solo suena con la página en pantalla; para el móvil usar
  Telegram.

### 9. Avisos por Raven (opcional)

Mensaje directo de un bot de Raven al agente asignado, con los mismos
eventos que Telegram. **Raven corre en este mismo servidor**, así que el
aviso no sale hacia ningún servicio externo: por eso es el único canal
donde se puede incluir el contenido del mensaje.

Ventaja sobre Telegram: el destinatario es el propio usuario de Frappe,
así que no hay que vincular ningún Chat ID.

**Configuración**

1. Instalar la app Raven en el bench y agregar a los agentes (necesitan
   un **Raven User** habilitado).
2. En Raven, crear un bot, por ejemplo "Alertas CORTEC".
3. `CORTEC Helpdesk Settings` → **Avisos por Raven** → ✅ Habilitar,
   elegir el bot y guardar. Opcionalmente, activar *Incluir el contenido
   del mensaje*.
4. **Raven → Enviar prueba** para confirmar que llega el mensaje directo.

**Limitación en el móvil:** para que suene con la pantalla bloqueada
hacen falta las notificaciones push de Frappe (*Push Notification
Settings*), que en sitios autoalojados dependen de un relay. Si no están
disponibles, Raven sirve para el escritorio y **Telegram sigue siendo el
canal del móvil**.

### Comparación de los tres canales

| | Raven | Telegram | Navegador |
| --- | --- | --- | --- |
| Sale del servidor | No | Sí | No |
| Puede incluir el mensaje | Sí, opcional | No | No |
| Vinculación por agente | Ninguna | Chat ID | Ninguna |
| Escritorio | Sí | Con Telegram Desktop | Sí, con /crm o /helpdesk abierto |
| Móvil con pantalla bloqueada | Solo con push (relay) | Sí | No |

### 10. Registro de consentimientos

Reproduce los «Acuerdos» y la pantalla «Consentimiento del usuario» de
los formularios de Bitrix24.

- **CORTEC User Agreement** — el texto de cada acuerdo. Cambiar el texto
  sube la versión. Los dos que usa la app son `politica-privacidad`
  (obligatorio) y `promociones` (opcional); `bench migrate` los crea con
  un texto provisional.
- **CORTEC Consent Record** — un registro por evento (Otorgado o
  Revocado) con fecha, canal, IP, URL, navegador, quién lo registró y
  una copia del texto aceptado. Nace enviado y no se puede modificar,
  cancelar ni borrar. Un error se corrige con otro evento.

Los campos `custom_acepta_promociones`, `custom_promociones_origen` y
`custom_consentimiento` del CRM Lead y del Contact son **de solo
lectura**: muestran el último evento del registro para cualquiera de
sus correos. Por eso el Contact que nace al convertir un Lead hereda el
consentimiento sin copiar nada.

Entradas al registro:

| Origen | Canal | Cómo |
| --- | --- | --- |
| Formulario del sitio | Formulario web | Worker `leads-intake` → `cortec_helpdesk.api.web_lead_intake` |
| Llamada, visita, correo | Teléfono / Presencial / Correo | Botón **Consentimientos → Registrar consentimiento** en el Lead o Contact (Desk) |
| Baja de la lista promocional | Baja por correo | Hooks de Email Group Member y Email Unsubscribe, más la conciliación diaria |
| Campos anteriores a v1.0.12 | Histórico | Patch `migrate_consent_fields` |
| Bitrix24 | Bitrix24 | `cortec_bitrix24.api.import_webform_consents` |

La lista promocional sigue el **último evento** de cada correo:

- Si es un Revocado, el correo se da de baja.
- Si es un Otorgado, se suscribe, salvo que tenga una baja global de
  correos del sitio.
- A quien estaba dado de baja solo lo reactiva un consentimiento **nuevo**
  (formulario web, teléfono, presencial o correo). Uno histórico
  (Bitrix24, Histórico) nunca pasa por encima de una baja.

El patch no toca la lista. La importación de Bitrix24 solo lo hace con
`'subscribe': 1`.

Durante la migración desde Bitrix24 (`frappe.flags.in_bitrix24_migration`),
los avisos a agentes y la creación de Leads por WhatsApp no actúan sobre
el histórico.

**Configuración**

1. `bench migrate`.
2. **CORTEC User Agreement** → abrir `politica-privacidad` y
   `promociones` y pegar el texto **exacto** que muestra el formulario
   del sitio. Cada sitio tiene los suyos: no son fixtures.
3. Desplegar el Worker `leads-intake` que llama a `web_lead_intake`.

**Limitaciones**

- Un Lead o Contact con consentimientos registrados no se puede borrar:
  Frappe no borra documentos enlazados desde registros enviados. Las
  solicitudes de supresión se atienden anonimizando (sección 10).
- El botón está en el formulario de Desk (`/app/crm-lead/...`). La
  interfaz `/crm` muestra los campos, pero todavía no tiene el botón.

### 11. Solicitudes de supresión y revocación (Ley 8968)

Cada solicitud de un titular se tramita en un **CORTEC Suppression
Request**, que solo usa el System Manager. El expediente no se puede
cancelar ni borrar.

**Plazos** (Decreto 37554-JP). Se calculan en días hábiles, descontando
sábados, domingos y los feriados de `CORTEC Helpdesk Settings → Feriados`:

| Plazo | Artículo |
| --- | --- |
| Responder: 5 días hábiles desde el día siguiente a la recepción | 18 |
| Pedir información adicional: una sola vez, dentro de esos 5 días. El plazo se pausa; si el titular no responde en 5 días hábiles, la solicitud se tiene por no presentada | 19 |
| Confirmar el cese del tratamiento, si lo pide: 3 días hábiles | 9 |
| Informar la revocación a los encargados: 5 días hábiles | 8 |

Una tarea diaria avisa a los System Manager de las solicitudes que vencen
y marca como «No presentada» las que esperaban información que no llegó.

**Flujo**

1. Crear la solicitud: tipo (Supresión o Revocación), fecha de recepción,
   medio de notificación (art. 17), cómo se acreditó la identidad
   (art. 15), y los correos, teléfonos y nombres del titular.
2. **Buscar datos**. Lista todo lo que se encontró, si el contenido de
   cada documento se conserva o se redacta, y propone B2B o B2C.
3. Elegir la resolución y enviar. Se ejecuta en segundo plano.
4. Revisar el **informe** y el **texto de la respuesta**, enviarla al
   titular y pulsar **Marcar respuesta enviada** (borra el medio de
   notificación).

**Resoluciones de una supresión**

| Resolución | Para | Efecto |
| --- | --- | --- |
| Desasociar | B2B | Anonimiza a la persona y conserva el contenido de tickets, negociaciones y correos, sin sus identificadores. La quita de la empresa (Ley 8968, art. 6.1) |
| Suprimir | B2C | Anonimiza a la persona y redacta el contenido y los adjuntos. Los documentos de una empresa cliente conservan su contenido sin los identificadores |
| Conservar — dato profesional | B2B | No anonimiza; revoca la publicidad y genera la negativa escrita (art. 22). **Apagada** hasta activar *Permitir «Conservar — dato profesional»* en Settings (Decreto art. 3, último párrafo: confirmar con el asesor legal) |

Una **Revocación** solo retira los consentimientos y da de baja de la
lista promocional; no anonimiza.

**Qué hace la supresión**

- Leads y Contacts del titular: datos sustituidos por `Suprimido <hash>`
  y `suprimido-<hash>@suprimido.invalid`. Los Contacts se renombran
  (`rename_doc` actualiza todos los enlaces).
- Correos, notas, comentarios, tareas, llamadas, WhatsApp y tickets: se
  reemplazan su correo, sus teléfonos y sus nombres completos dentro del
  texto, o se redacta el contenido.
- Se borran: adjuntos de lo redactado, Version, Activity Log, cola de
  correo, Deleted Document, miembros de listas y bajas de correo.
- Usuarios del portal (Website User): anonimizados y desactivados.
- Registro de consentimientos: se registra un Revocado «Supresión». El
  correo pasa a su HMAC y se borran la IP, la URL y el navegador. Así
  se conserva la prueba del consentimiento (Decreto art. 6) sin guardar
  el correo en claro.
- El expediente guarda solo los HMAC de los identificadores.
- Todo documento tocado queda en **CORTEC Suppressed Document**, y
  cortec_bitrix24 no vuelve a escribir en él.

El HMAC usa la `encryption_key` del sitio. **Sin esa clave no se puede
comprobar si un correo fue suprimido**: debe estar en las copias de
seguridad de `site_config.json`.

**Límites**

- Solo se reemplazan nombres de dos o más palabras. Apodos, firmas
  escaneadas, cédulas y datos escritos de otra forma no se detectan:
  revise en «Buscar datos» lo que se marcó «conservar».
- El informe lista siempre los pasos que Frappe no puede hacer: copias
  de seguridad, buzones IMAP, avisos ya enviados por Telegram o Raven,
  Error Log y exportaciones previas.
- Usuarios internos de Frappe (agentes): se tratan a mano.

## Flujo de un ticket

```
1. Cliente envía correo a correo1@dominio.com
2. Frappe crea HD Ticket → auto_assign_ticket se ejecuta
3. Sistema resuelve: email → Contacto → Cliente → account_manager
4. Ticket asignado al agente automáticamente
5. Agente y supervisor reciben notificación desde correo1@
6. Helpdesk envía acuse de recibo al cliente desde correo1@
7. Agente responde desde UI → cliente recibe desde correo1@
8. Si no se resuelve agente → los supervisores (Agent Manager) reciben alerta
```

## Email routing

| Doctype                      | Cuenta                  |
| ---------------------------- | ----------------------- |
| HD Ticket                    | correo1@dominio.com     |
| CRM Lead, CRM Deal, Prospect | correo2@dominio.com     |
| Quotation, Sales Order       | correo2@dominio.com     |
| Sales Invoice, Payment Entry | no-responda@dominio.com |

Editar `DOCTYPE_EMAIL_MAP` en `communication.py` para agregar rutas.

## Diagnóstico

### Verificar cadena email → agente

```python
import frappe
from cortec_helpdesk.overrides.hd_ticket import (
    _find_contact_by_email,
    _find_customer_for_contact,
)

email = "cliente@empresa.com"
contact = _find_contact_by_email(email)
customer = _find_customer_for_contact(contact) if contact else None
manager = frappe.db.get_value("Customer", customer, "account_manager") if customer else None
print(f"{email} → {contact} → {customer} → {manager}")
```

### Diagnóstico masivo de contactos

```python
import frappe
contactos = frappe.db.sql("""
    SELECT ce.email_id, dl.link_name AS cliente, cust.account_manager
    FROM `tabContact` c
    JOIN `tabContact Email` ce ON ce.parent = c.name
    LEFT JOIN `tabDynamic Link` dl
        ON dl.parent = c.name AND dl.parenttype = 'Contact'
        AND dl.link_doctype = 'Customer'
    LEFT JOIN `tabCustomer` cust ON cust.name = dl.link_name
    ORDER BY ce.email_id
""", as_dict=True)

for c in contactos:
    if c.cliente and c.account_manager:
        print(f"  OK  {c.email_id} → {c.cliente} → {c.account_manager}")
    elif c.cliente:
        print(f"  !!  {c.email_id} → {c.cliente} → SIN MANAGER")
    else:
        print(f"  XX  {c.email_id} → SIN CLIENTE")
```

### Verificar email routing

```python
from cortec_helpdesk.overrides.communication import (
    _get_email_account_name, DOCTYPE_EMAIL_MAP,
)
for dt, email in DOCTYPE_EMAIL_MAP.items():
    acc = _get_email_account_name(email)
    print(f"  {'OK' if acc else 'XX'}  {dt} → {email} → {acc}")
```

### Leads duplicados por WhatsApp

Lista los números con más de un CRM Lead, para revisarlos y unificarlos a mano:

```python
import frappe
filas = frappe.db.sql("""
    SELECT RIGHT(REGEXP_REPLACE(mobile_no, '[^0-9]', ''), 8) AS tel,
           COUNT(*) AS n, GROUP_CONCAT(name) AS leads
    FROM `tabCRM Lead`
    WHERE IFNULL(mobile_no, '') != ''
    GROUP BY tel HAVING n > 1
    ORDER BY n DESC
""", as_dict=True)

for f in filas:
    print(f"{f.tel}  x{f.n}  {f.leads}")
```

Desde v1.0.12 ya no se generan nuevos duplicados: un WhatsApp de un número con
un Lead o Deal en seguimiento se vincula al existente.

### Unificar los duplicados que ya existen

`cortec_helpdesk.merge_whatsapp_leads` conserva el Lead **más antiguo** de cada
número, le mueve el historial (WhatsApp, correos, notas, tareas, llamadas,
comentarios y adjuntos) y marca los demás como **Junk**. No borra nada.

```bash
bench --site sitio.dominio.com backup     # primero, siempre
bench --site sitio.dominio.com console
```

```python
from cortec_helpdesk.merge_whatsapp_leads import report, merge

report()                                  # lista los grupos duplicados
merge()                                   # simulacro: no toca nada
merge(dry_run=False)                      # aplica los cambios
merge(phone="61591066", dry_run=False)    # un solo número
```

Los grupos con algún Lead ya convertido a Deal se omiten y se reportan: detrás
hay una negociación, así que se revisan a mano.

### Ver errores recientes

```python
import frappe
frappe.get_all("Error Log",
    filters={"title": ["like", "%CORTEC%"]},
    fields=["title", "error", "creation"],
    order_by="creation desc", limit=10)
```

## Notas

- No modifica doctypes existentes de Frappe, Helpdesk, CRM o ERPNext.
- Compatible con Frappe v15/v16 y Frappe Helpdesk v2.x.
- Notificaciones usan `now=True` (envío inmediato). Para alto volumen
  cambiar a `now=False` para usar la cola de email.
