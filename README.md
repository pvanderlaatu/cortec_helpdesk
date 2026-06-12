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
        └── communication.py     # Email routing por doctype
```

## Instalación

```bash
bench get-app cortec_helpdesk https://github.com/pvanderlaatu/cortec_helpdesk
bench --site sistema.tecnocr.net install-app cortec_helpdesk
bench --site sistema.tecnocr.net migrate
bench restart
```

## Configuración requerida

### 1. Cuentas de correo

| Cuenta | Default Outgoing | Default Incoming | Saliente | Entrante |
|--------|:---:|:---:|:---:|:---:|
| no-reply@tecnocr.net | ✅ | ❌ | ✅ | ❌ |
| soporte@tecnocr.net | ❌ | ✅ | ✅ | ✅ |
| crm@tecnocr.net | ❌ | ❌ | ✅ | ✅ |

Configuración adicional en **soporte@** y **crm@** (pestaña Saliente):
- ✅ Utilice siempre esta dirección como dirección de remitente
- ✅ Utilizar siempre este nombre como nombre de remitente

Configuración en **soporte@** (pestaña Entrante):
- Opción de Sincronizar: NO VISTO
- Carpeta IMAP: solo INBOX → Append to: HD Ticket
- No vincular INBOX.Sent
- ✅ Habilitar la vinculación automática en documentos

### 2. Roles

| Rol | Propósito | Visibilidad |
|-----|-----------|-------------|
| System Manager | Administrador | Todos los tickets |
| HD Manager | Supervisor helpdesk | Todos los tickets |
| HD Agent Lead | Persona asignadora | Todos los tickets |
| HD Agent | Agente de soporte | Solo tickets asignados |

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

## Flujo de un ticket

```
1. Cliente envía correo a soporte@tecnocr.net
2. Frappe crea HD Ticket → auto_assign_ticket se ejecuta
3. Sistema resuelve: email → Contacto → Cliente → account_manager
4. Ticket asignado al agente automáticamente
5. Agente y supervisor reciben notificación desde soporte@
6. Helpdesk envía acuse de recibo al cliente desde soporte@
7. Agente responde desde UI → cliente recibe desde soporte@
8. Si no se resuelve agente → HD Agent Lead recibe alerta
```

## Email routing

| Doctype | Cuenta |
|---------|--------|
| HD Ticket | soporte@tecnocr.net |
| CRM Lead, CRM Deal, Prospect | crm@tecnocr.net |
| Quotation, Sales Order | crm@tecnocr.net |
| Sales Invoice, Payment Entry | no-reply@tecnocr.net |

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
