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
cortec_helpdesk.overrides.communication
========================================
Intercepta cada Communication saliente y asigna la cuenta de correo
correcta según el doctype de origen.

Esto garantiza que:
  - Respuestas a tickets     → salen por la cuenta configurada para Helpdesk
  - Emails de CRM            → salen por la cuenta configurada para CRM
  - Todo lo demás            → Default Outgoing (no-reply@)

Las direcciones de correo NO están hardcodeadas: se eligen desde la
interfaz web en CORTEC Helpdesk Settings (ver
cortec_helpdesk.doctype.cortec_helpdesk_settings). Editar
DOCTYPE_CATEGORY_MAP solo si se necesita enrutar un doctype nuevo hacia
una de esas dos categorías.
"""

import frappe

from cortec_helpdesk.cortec_helpdesk.doctype.cortec_helpdesk_settings.cortec_helpdesk_settings import (
    get_configured_email,
    get_configured_email_account,
)


# ---------------------------------------------------------------------------
# Mapeo doctype → categoría ("helpdesk" o "crm")
# ---------------------------------------------------------------------------

DOCTYPE_CATEGORY_MAP = {
    # Frappe Helpdesk
    "HD Ticket": "helpdesk",

    # Frappe CRM
    "CRM Lead": "crm",
    "CRM Deal": "crm",

    # Agregar más rutas según necesidad:
    # "CRM Task": "crm",
}


# ---------------------------------------------------------------------------
# Hook principal
# ---------------------------------------------------------------------------

def route_email_by_doctype(doc, method=None):
    """
    Hook ``before_insert`` en Communication.

    Solo actúa cuando:
      1. Es un email saliente (sent_or_received == 'Sent')
      2. Tiene un reference_doctype en el mapeo
      3. Hay una cuenta configurada en CORTEC Helpdesk Settings para
         esa categoría

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

    category = DOCTYPE_CATEGORY_MAP.get(reference_doctype)
    if not category:
        return

    account_name = get_configured_email_account(category)
    target_email = get_configured_email(category)
    if not account_name or not target_email:
        frappe.log_error(
            title="CORTEC Email Routing: cuenta no configurada",
            message=(
                f"Communication para {reference_doctype}/"
                f"{doc.get('reference_name')}: no hay una cuenta de correo "
                f"configurada para la categoría '{category}' en "
                f"CORTEC Helpdesk Settings."
            ),
        )
        return

    doc.sender = target_email
    doc.email_account = account_name

    frappe.logger("cortec_helpdesk").info(
        f"Email routing: {reference_doctype}/"
        f"{doc.get('reference_name')} → {target_email} ({account_name})"
    )


def route_email_queue_by_doctype(doc, method=None):
    """
    Hook ``before_insert`` en Email Queue.

    Frappe encola el email ANTES de crear la Communication, por lo que
    el hook de Communication llega tarde para las notificaciones del
    helpdesk (acknowledgments, etc.).  Este hook actúa directamente en
    la cola y garantiza que el sender correcto llegue al servidor SMTP.
    """
    reference_doctype = doc.get("reference_doctype")
    if not reference_doctype:
        return

    category = DOCTYPE_CATEGORY_MAP.get(reference_doctype)
    if not category:
        return

    target_email = get_configured_email(category)
    if not target_email:
        frappe.log_error(
            title="CORTEC Email Routing (Queue): cuenta no configurada",
            message=(
                f"Email Queue para {reference_doctype}/"
                f"{doc.get('reference_name')}: no hay una cuenta de correo "
                f"configurada para la categoría '{category}' en "
                f"CORTEC Helpdesk Settings."
            ),
        )
        return

    doc.sender = target_email

    frappe.logger("cortec_helpdesk").info(
        f"Email Queue routing: {reference_doctype}/"
        f"{doc.get('reference_name')} → {target_email}"
    )
