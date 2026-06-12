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

"""
cortec_helpdesk.overrides.communication
========================================
Intercepta cada Communication saliente y asigna la cuenta de correo
correcta según el doctype de origen.

Esto garantiza que:
  - Respuestas a tickets     → salen por soporte@tecnocr.net
  - Emails de CRM            → salen por crm@tecnocr.net
  - Facturas / cotizaciones  → salen por no-reply@tecnocr.net
  - Todo lo demás            → Default Outgoing (no-reply@)

Configuración del mapeo
-----------------------
Editar DOCTYPE_EMAIL_MAP para agregar nuevas rutas.
Reiniciar bench después de cambios.
"""

import frappe


# ---------------------------------------------------------------------------
# Mapeo doctype → dirección de correo
# ---------------------------------------------------------------------------

DOCTYPE_EMAIL_MAP = {
    # Frappe Helpdesk
    "HD Ticket": "soporte@tecnocr.net",

    # Frappe CRM
    "CRM Lead": "crm@tecnocr.net",
    "CRM Deal": "crm@tecnocr.net",
    "Prospect": "crm@tecnocr.net",

    # ERPNext Ventas
    "Quotation": "crm@tecnocr.net",
    "Sales Order": "crm@tecnocr.net",

    # ERPNext Facturación y contabilidad
    "Sales Invoice": "no-reply@tecnocr.net",
    "Payment Entry": "no-reply@tecnocr.net",

    # Agregar más rutas según necesidad:
    # "Purchase Order": "compras@tecnocr.net",
}


# ---------------------------------------------------------------------------
# Cache: email → nombre de Email Account en Frappe
# ---------------------------------------------------------------------------

_email_account_cache = {}


def _get_email_account_name(email_address: str) -> str | None:
    """
    Dado un email como 'soporte@tecnocr.net', retorna el nombre
    del documento Email Account en Frappe.
    """
    if email_address in _email_account_cache:
        return _email_account_cache[email_address]

    account_name = frappe.db.get_value(
        "Email Account",
        {"email_id": email_address, "enable_outgoing": 1},
        "name",
    )

    if account_name:
        _email_account_cache[email_address] = account_name

    return account_name


# ---------------------------------------------------------------------------
# Hook principal
# ---------------------------------------------------------------------------

def route_email_by_doctype(doc, method=None):
    """
    Hook ``before_insert`` en Communication.

    Solo actúa cuando:
      1. Es un email saliente (sent_or_received == 'Sent')
      2. Tiene un reference_doctype en el mapeo
      3. La cuenta de email existe y tiene saliente habilitado

    Si alguna condición falla, no hace nada y Frappe usa
    su lógica por defecto (Default Outgoing).
    """
    if doc.communication_medium != "Email":
        return
    if doc.sent_or_received != "Sent":
        return

    reference_doctype = doc.get("reference_doctype")
    if not reference_doctype:
        return

    target_email = DOCTYPE_EMAIL_MAP.get(reference_doctype)
    if not target_email:
        return

    account_name = _get_email_account_name(target_email)
    if not account_name:
        frappe.log_error(
            title="CORTEC Email Routing: cuenta no encontrada",
            message=(
                f"Communication para {reference_doctype}/"
                f"{doc.get('reference_name')}: la cuenta "
                f"{target_email} no existe o no tiene saliente habilitado."
            ),
        )
        return

    doc.sender = target_email
    doc.email_account = account_name

    frappe.logger("cortec_helpdesk").info(
        f"Email routing: {reference_doctype}/"
        f"{doc.get('reference_name')} → {target_email} ({account_name})"
    )
