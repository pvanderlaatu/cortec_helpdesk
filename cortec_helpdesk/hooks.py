# Copyright (C) 2025 Corporación de Tecnología CORTEC S.R.L.
# SPDX-License-Identifier: AGPL-3.0-or-later

from . import __version__ as app_version

app_name = "cortec_helpdesk"
app_title = "CORTEC Helpdesk"
app_publisher = "Corporación de Tecnología CORTEC S.R.L."
app_description = (
    "Customizaciones de Frappe Helpdesk para CORTEC: "
    "control de visibilidad por agente, asignación automática "
    "de tickets por cliente, email routing por doctype y avisos "
    "(Telegram y sonido en el navegador) de WhatsApp y correos entrantes."
)
app_email = "soporte@tecnocr.net"
app_license = "AGPL-3.0"

# ---------------------------------------------------------------------------
# Apps de las que depende esta app. No incluye ERPNext (licencia GPL-3.0)
# a propósito, para evitar combinar código GPL-3.0 con AGPL-3.0.
# ---------------------------------------------------------------------------
required_apps = [
    "helpdesk",
    "crm",
    "frappe_whatsapp",
    "whatsapp_chat",
    "telephony",
]

# ---------------------------------------------------------------------------
# Fixtures — datos exportados/importados con `bench migrate` / `export-fixtures`
# ---------------------------------------------------------------------------
fixtures = [
    {
        "doctype": "Custom Field",
        "filters": [
            [
                "name",
                "in",
                [
                    "Contact-custom_account_manager",
                    "HD Customer-custom_account_manager",
                    "CRM Lead-custom_comentarios",
                    "CRM Lead-custom_consentimiento",
                    "CRM Lead-custom_acepta_promociones",
                    "CRM Lead-custom_promociones_origen",
                    "Contact-custom_acepta_promociones",
                    "Contact-custom_promociones_origen",
                ],
            ]
        ],
    },
    {
        "doctype": "Email Group",
        "filters": [["title", "=", "Promociones CORTEC"]],
    },
]

# ---------------------------------------------------------------------------
# Permisos a nivel SQL — restringe qué tickets puede VER cada agente
# ---------------------------------------------------------------------------
permission_query_conditions = {
    "HD Ticket": "cortec_helpdesk.overrides.hd_ticket.get_permission_query_conditions",
}

# ---------------------------------------------------------------------------
# Permisos a nivel de documento — restringe acceso directo por URL / API
# ---------------------------------------------------------------------------
has_permission = {
    "HD Ticket": "cortec_helpdesk.overrides.hd_ticket.has_permission",
}

# ---------------------------------------------------------------------------
# Renderers de página — inserta el script de alertas audibles en /crm y
# /helpdesk sin modificar esas apps (ver overrides/browser_alerts.py)
# ---------------------------------------------------------------------------
page_renderer = [
    "cortec_helpdesk.overrides.browser_alerts.AlertsTemplatePage",
]

# ---------------------------------------------------------------------------
# Clases propias — WhatsApp Message sube sus adjuntos a Meta en vez de
# enlazarlos, que es la única forma de enviar archivos privados
# (ver overrides/whatsapp_message.py)
# ---------------------------------------------------------------------------
override_doctype_class = {
    "WhatsApp Message": "cortec_helpdesk.overrides.whatsapp_message.CORTECWhatsAppMessage",
}

# ---------------------------------------------------------------------------
# Document Events
# ---------------------------------------------------------------------------
doc_events = {
    "HD Ticket": {
        "after_insert": "cortec_helpdesk.overrides.hd_ticket.auto_assign_ticket",
    },
    "Communication": {
        "before_insert": "cortec_helpdesk.overrides.communication.route_email_by_doctype",
        "after_insert": "cortec_helpdesk.overrides.alerts.on_communication",
    },
    "Email Queue": {
        "before_insert": "cortec_helpdesk.overrides.communication.route_email_queue_by_doctype",
    },
    "WhatsApp Message": {
        # El orden importa: los avisos deben ver el Lead que
        # route_unclaimed_message crea para mensajes sin vínculo abierto.
        "after_insert": [
            "cortec_helpdesk.overrides.whatsapp.route_unclaimed_message",
            "cortec_helpdesk.overrides.alerts.on_whatsapp_message",
        ],
    },
    "CRM Lead": {
        "validate": "cortec_helpdesk.overrides.consent.sync_consent_fields",
    },
    "Contact": {
        "validate": "cortec_helpdesk.overrides.consent.sync_consent_fields",
    },
    "Email Group Member": {
        "validate": "cortec_helpdesk.overrides.email_group_member.require_registered_consent",
        "on_update": "cortec_helpdesk.overrides.email_group_member.record_unsubscribe",
    },
    "Email Unsubscribe": {
        "after_insert": "cortec_helpdesk.overrides.email_group_member.record_global_unsubscribe",
    },
}

# ---------------------------------------------------------------------------
# Botón «Registrar consentimiento» en el formulario de Desk
# ---------------------------------------------------------------------------
doctype_js = {
    "CRM Lead": "public/js/consent_record.js",
    "Contact": "public/js/consent_record.js",
}

# ---------------------------------------------------------------------------
# Tareas programadas
# ---------------------------------------------------------------------------
scheduler_events = {
    # Bajas de la lista promocional que no pasaron por el hook de
    # Email Group Member (ver consent_log.reconcile_unsubscribes).
    "daily": [
        "cortec_helpdesk.consent_log.reconcile_unsubscribes",
        # Plazos de las solicitudes de datos personales (privacy.py).
        "cortec_helpdesk.privacy.daily_privacy_tasks",
    ],
}
