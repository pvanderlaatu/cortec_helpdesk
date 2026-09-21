# Copyright (C) 2025 Corporación de Tecnología CORTEC S.R.L.
# SPDX-License-Identifier: AGPL-3.0-or-later
#
# This file is part of CORTEC Helpdesk.
#
# CORTEC Helpdesk is free software: you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# CORTEC Helpdesk is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the
# GNU Affero General Public License for more details.
#
# You should have received a copy of the GNU Affero General Public License
# along with CORTEC Helpdesk. If not, see <https://www.gnu.org/licenses/>.
from __future__ import annotations

"""
cortec_helpdesk.overrides.alert_utils
======================================
Helpers comunes a los canales de aviso (Telegram y alertas en el
navegador), para que ambos decidan igual qué documentos avisan, a quién
y cómo se muestran.
"""

from urllib.parse import quote

import frappe
from frappe.utils import get_url, parse_addr


# Doctypes cuyos correos entrantes generan aviso: etiqueta y ruta en la UI.
REFERENCE_DOCTYPES = {
    "CRM Lead": {"label": "Lead", "route": "/crm/leads/{name}"},
    "CRM Deal": {"label": "Deal", "route": "/crm/deals/{name}"},
    "HD Ticket": {"label": "Ticket #", "route": "/helpdesk/tickets/{name}"},
}

# Campos que se prueban, en orden, para mostrar el nombre del cliente.
DISPLAY_NAME_FIELDS = {
    "CRM Lead": ("lead_name", "organization", "first_name"),
    "CRM Deal": ("organization", "lead_name"),
    "HD Ticket": ("customer", "contact", "raised_by"),
}


def get_assignees(doctype: str, name: str) -> list[str]:
    """Usuarios asignados al documento (campo _assign)."""
    assign = frappe.db.get_value(doctype, name, "_assign")
    if not assign:
        return []
    try:
        return frappe.parse_json(assign) or []
    except Exception:
        return []


def get_display_name(doctype: str, name: str) -> str | None:
    """Primer campo con valor de DISPLAY_NAME_FIELDS para el documento."""
    meta = frappe.get_meta(doctype)
    fields = [f for f in DISPLAY_NAME_FIELDS.get(doctype, ()) if meta.has_field(f)]
    if not fields:
        return None

    values = frappe.db.get_value(doctype, name, fields, as_dict=True) or {}
    for field in fields:
        if values.get(field):
            return str(values[field])
    return None


def is_internal_sender(sender: str | None) -> bool:
    """True si el remitente es un usuario activo del sistema (un agente)."""
    email = parse_addr(sender or "")[1]
    if not email:
        return False
    return bool(
        frappe.db.exists("User", {"email": email, "enabled": 1, "user_type": "System User"})
    )


def build_link(doctype: str, name: str, absolute: bool = True) -> str:
    """
    URL del documento en la UI de CRM o Helpdesk. Absoluta para avisos
    fuera del sitio (Telegram); relativa para el navegador, que ya está
    en el dominio correcto.
    """
    route = REFERENCE_DOCTYPES[doctype]["route"].format(name=quote(str(name)))
    return get_url(route) if absolute else route


def build_reference_label(doctype: str, name: str) -> str:
    """Texto plano tipo 'Lead CRM-LEAD-0001' o 'Ticket #123'."""
    label = REFERENCE_DOCTYPES[doctype]["label"]
    separator = "" if label.endswith("#") else " "
    return f"{label}{separator}{name}"
