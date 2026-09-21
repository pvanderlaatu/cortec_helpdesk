# CORTEC Helpdesk

Customizaciones de Frappe Helpdesk para Corporación de Tecnología CORTEC S.R.L.

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
   /crm y /helpdesk con los mismos eventos (opcional, independiente de
   Telegram).

## Estructura

```
cortec_helpdesk/
├── LICENSE
├── setup.py
├── requirements.txt
├── README.md
└── cortec_helpdesk/
    ├── __init__.py
    ├── hooks.py
    └── overrides/
        ├── __init__.py
        ├── hd_ticket.py         # Permisos y asignación automática
        ├── communication.py     # Email routing por doctype
        ├── alert_utils.py       # Helpers comunes de avisos
        ├── telegram.py          # Avisos por Telegram (WhatsApp y correo)
        └── browser_alerts.py    # Alertas audibles en /crm y /helpdesk
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

| Rol            | Propósito           | Visibilidad            |
| -------------- | ------------------- | ---------------------- |
| System Manager | Administrador       | Todos los tickets      |
| HD Manager     | Supervisor helpdesk | Todos los tickets      |
| HD Agent Lead  | Persona asignadora  | Todos los tickets      |
| HD Agent       | Agente de soporte   | Solo tickets asignados |

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

### 6. Avisos por Telegram (opcional)

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

### 7. Alertas en el navegador (opcional)

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

## Flujo de un ticket

```
1. Cliente envía correo a correo1@dominio.com
2. Frappe crea HD Ticket → auto_assign_ticket se ejecuta
3. Sistema resuelve: email → Contacto → Cliente → account_manager
4. Ticket asignado al agente automáticamente
5. Agente y supervisor reciben notificación desde correo1@
6. Helpdesk envía acuse de recibo al cliente desde correo1@
7. Agente responde desde UI → cliente recibe desde correo1@
8. Si no se resuelve agente → HD Agent Lead recibe alerta
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
