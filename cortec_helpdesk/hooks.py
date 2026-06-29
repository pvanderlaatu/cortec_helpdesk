# Copyright (C) 2025 Corporación de Tecnología CORTEC S.R.L.
# SPDX-License-Identifier: AGPL-3.0-or-later

from . import __version__ as app_version

app_name = "cortec_helpdesk"
app_title = "CORTEC Helpdesk"
app_publisher = "Corporación de Tecnología CORTEC S.R.L."
app_description = (
    "Customizaciones de Frappe Helpdesk para CORTEC: "
    "control de visibilidad por agente, asignación automática "
    "de tickets por cliente, y email routing por doctype."
)
app_email = "soporte@tecnocr.net"
app_license = "AGPL-3.0"

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
# Document Events
# ---------------------------------------------------------------------------
doc_events = {
    "HD Ticket": {
        "after_insert": "cortec_helpdesk.overrides.hd_ticket.auto_assign_ticket",
    },
    "Communication": {
        "before_insert": "cortec_helpdesk.overrides.communication.route_email_by_doctype",
    },
    "Email Queue": {
        "before_insert": "cortec_helpdesk.overrides.communication.route_email_queue_by_doctype",
    },
}
