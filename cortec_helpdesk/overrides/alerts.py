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
cortec_helpdesk.overrides.alerts
=================================
Despachador de los avisos al agente asignado cuando entra un WhatsApp o
un correo de un cliente.

Resuelve el evento UNA sola vez y lo reparte a los canales de servidor
(Telegram y Raven), que se habilitan por separado en CORTEC Helpdesk
Settings. El tercer canal, el sonido en el navegador, no pasa por aquí:
lo consulta el propio navegador (ver browser_alerts.py).

on_whatsapp_message / on_communication — hooks after_insert. Solo
encolan, para no retrasar el webhook de WhatsApp ni la lectura IMAP.
dispatch_whatsapp / dispatch_email    — trabajos en cola. Releen el
documento, así ya ven el Lead creado por route_unclaimed_message y la
asignación hecha por auto_assign_ticket o por una Assignment Rule.
"""

import frappe
from frappe.utils import parse_addr

from cortec_helpdesk.overrides import raven, telegram
from cortec_helpdesk.overrides.alert_utils import (
    REFERENCE_DOCTYPES,
    get_assignees,
    get_display_name,
    is_internal_sender,
)


# Canales de servidor, en orden de envío. Cada módulo expone
# is_enabled_for(kind) y notify(event, assignees).
CHANNELS = (telegram, raven)


# ---------------------------------------------------------------------------
# Hooks
# ---------------------------------------------------------------------------

def on_whatsapp_message(doc, method: str = None) -> None:
    """
    Hook ``after_insert`` en WhatsApp Message. Debe ir DESPUÉS de
    route_unclaimed_message en hooks.py.

    Defensivo: un error aquí nunca debe interrumpir la recepción del
    mensaje (corre en el flujo del webhook entrante).
    """
    try:
        # Histórico que llega por la migración desde Bitrix24: ni avisos
        # ni Leads nuevos por mensajes de hace años.
        if frappe.flags.get("in_bitrix24_migration"):
            return
        if doc.type != "Incoming":
            return
        if not _any_channel_enabled("whatsapp"):
            return

        frappe.enqueue(
            "cortec_helpdesk.overrides.alerts.dispatch_whatsapp",
            queue="short",
            message_name=doc.name,
            enqueue_after_commit=True,
        )
    except Exception as e:
        frappe.log_error(
            title="CORTEC Avisos: error encolando aviso de WhatsApp",
            message=f"WhatsApp Message {doc.name}\nError: {str(e)}",
        )


def on_communication(doc, method: str = None) -> None:
    """
    Hook ``after_insert`` en Communication. Solo correos recibidos que
    referencian un doctype de REFERENCE_DOCTYPES.
    """
    try:
        # Histórico que llega por la migración desde Bitrix24: ni avisos
        # ni Leads nuevos por mensajes de hace años.
        if frappe.flags.get("in_bitrix24_migration"):
            return
        if doc.communication_medium != "Email":
            return
        if doc.sent_or_received != "Received":
            return
        if doc.get("reference_doctype") not in REFERENCE_DOCTYPES:
            return
        if not _any_channel_enabled("email"):
            return

        frappe.enqueue(
            "cortec_helpdesk.overrides.alerts.dispatch_email",
            queue="short",
            communication_name=doc.name,
            enqueue_after_commit=True,
        )
    except Exception as e:
        frappe.log_error(
            title="CORTEC Avisos: error encolando aviso de correo",
            message=f"Communication {doc.name}\nError: {str(e)}",
        )


# ---------------------------------------------------------------------------
# Trabajos en cola
# ---------------------------------------------------------------------------

def dispatch_whatsapp(message_name: str) -> None:
    """Reparte el aviso de un WhatsApp Message entrante a los canales."""
    meta = frappe.get_meta("WhatsApp Message")
    fields = ["reference_doctype", "reference_name"] + [
        f for f in ("profile_name", "from", "message") if meta.has_field(f)
    ]
    message = frappe.db.get_value("WhatsApp Message", message_name, fields, as_dict=True)
    if not message or message.reference_doctype not in ("CRM Lead", "CRM Deal"):
        # Sin Lead/Deal no hay agente asignado a quien avisar.
        return

    customer = message.get("profile_name") or get_display_name(
        message.reference_doctype, message.reference_name
    ) or message.get("from")

    _dispatch({
        "kind": "whatsapp",
        "doctype": message.reference_doctype,
        "name": message.reference_name,
        "customer": customer,
        # Solo lo usa el canal que tenga habilitado incluir el contenido.
        "text": message.get("message"),
    })


def dispatch_email(communication_name: str) -> None:
    """Reparte el aviso de un correo recibido a los canales."""
    comm = frappe.db.get_value(
        "Communication", communication_name,
        ["reference_doctype", "reference_name", "sender", "sender_full_name", "subject"],
        as_dict=True,
    )
    if not comm or comm.reference_doctype not in REFERENCE_DOCTYPES:
        return

    if is_internal_sender(comm.sender):
        # Respuesta o copia de un propio agente: no es un cliente.
        return

    customer = get_display_name(
        comm.reference_doctype, comm.reference_name
    ) or comm.sender_full_name or parse_addr(comm.sender or "")[1]

    _dispatch({
        "kind": "email",
        "doctype": comm.reference_doctype,
        "name": comm.reference_name,
        "customer": customer,
        "text": comm.get("subject"),
    })


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _dispatch(event: dict) -> None:
    """Envía el evento por cada canal habilitado; un canal caído no afecta al otro."""
    assignees = get_assignees(event["doctype"], event["name"])
    if not assignees:
        return

    for channel in CHANNELS:
        try:
            if channel.is_enabled_for(event["kind"]):
                channel.notify(event, assignees)
        except Exception as e:
            frappe.log_error(
                title=f"CORTEC Avisos: fallo del canal {channel.__name__.rsplit('.', 1)[-1]}",
                message=(
                    f"{event['doctype']} {event['name']} ({event['kind']})\n"
                    f"Error: {str(e)}"
                ),
            )


def _any_channel_enabled(kind: str) -> bool:
    for channel in CHANNELS:
        try:
            if channel.is_enabled_for(kind):
                return True
        except Exception:
            continue
    return False
