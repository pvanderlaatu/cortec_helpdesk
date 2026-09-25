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
cortec_helpdesk.overrides.telegram
===================================
Avisos por Telegram al agente asignado cuando entra un WhatsApp o un
correo de un cliente. Frappe CRM/Helpdesk solo avisan en pantalla; un
mensaje de Telegram suena en el móvil aunque esté bloqueado.

on_whatsapp_message — after_insert de WhatsApp Message (Incoming).
on_communication    — after_insert de Communication (correo recibido en
                      CRM Lead, CRM Deal o HD Ticket).

Ambos hooks solo encolan el envío (después del commit) para no retrasar
el webhook de WhatsApp ni la lectura IMAP. El trabajo en cola relee los
documentos, así ya ve el Lead creado por route_unclaimed_message y la
asignación hecha por auto_assign_ticket o por una Assignment Rule.

Todo es opcional: interruptor general y por tipo en CORTEC Helpdesk
Settings, y por agente en la tabla "Agentes vinculados".

Privacidad: el aviso lleva solo el documento, el nombre del cliente y
el enlace; nunca el texto del mensaje ni el asunto del correo.
"""

import frappe
import requests
from frappe import _
from frappe.utils import cint, escape_html, parse_addr

from cortec_helpdesk.cortec_helpdesk.doctype.cortec_helpdesk_settings.cortec_helpdesk_settings import (
    get_telegram_settings,
)
from cortec_helpdesk.overrides.alert_utils import (
    REFERENCE_DOCTYPES,
    build_link,
    build_reference_label,
    get_assignees,
    get_display_name,
    is_internal_sender,
)


TELEGRAM_API_URL = "https://api.telegram.org/bot{token}/{method}"
REQUEST_TIMEOUT = 10

# Quién puede usar los botones de Telegram en Settings. "Agent Manager"
# es el rol de supervisor de Frappe Helpdesk.
MANAGER_ROLES = ("System Manager", "Agent Manager", "HD Manager")


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
        if doc.type != "Incoming":
            return

        settings = get_telegram_settings()
        if not settings or not settings.telegram_notify_whatsapp:
            return

        frappe.enqueue(
            "cortec_helpdesk.overrides.telegram.send_whatsapp_alert",
            queue="short",
            message_name=doc.name,
            enqueue_after_commit=True,
        )
    except Exception as e:
        frappe.log_error(
            title="CORTEC Telegram: error encolando aviso de WhatsApp",
            message=f"WhatsApp Message {doc.name}\nError: {str(e)}",
        )


def on_communication(doc, method: str = None) -> None:
    """
    Hook ``after_insert`` en Communication. Solo correos recibidos que
    referencian un doctype de REFERENCE_DOCTYPES.
    """
    try:
        if doc.communication_medium != "Email":
            return
        if doc.sent_or_received != "Received":
            return
        if doc.get("reference_doctype") not in REFERENCE_DOCTYPES:
            return

        settings = get_telegram_settings()
        if not settings or not settings.telegram_notify_email:
            return

        frappe.enqueue(
            "cortec_helpdesk.overrides.telegram.send_email_alert",
            queue="short",
            communication_name=doc.name,
            enqueue_after_commit=True,
        )
    except Exception as e:
        frappe.log_error(
            title="CORTEC Telegram: error encolando aviso de correo",
            message=f"Communication {doc.name}\nError: {str(e)}",
        )


# ---------------------------------------------------------------------------
# Trabajos en cola
# ---------------------------------------------------------------------------

def send_whatsapp_alert(message_name: str) -> None:
    """Envía el aviso de un WhatsApp Message entrante a sus agentes."""
    settings = get_telegram_settings()
    if not settings or not settings.telegram_notify_whatsapp:
        return

    meta = frappe.get_meta("WhatsApp Message")
    fields = ["reference_doctype", "reference_name"] + [
        f for f in ("profile_name", "from") if meta.has_field(f)
    ]
    message = frappe.db.get_value("WhatsApp Message", message_name, fields, as_dict=True)
    if not message or message.reference_doctype not in ("CRM Lead", "CRM Deal"):
        # Sin Lead/Deal no hay agente asignado a quien avisar.
        return

    customer = message.get("profile_name") or get_display_name(
        message.reference_doctype, message.reference_name
    ) or message.get("from")

    _notify_assignees(
        settings,
        flag="notify_whatsapp",
        title="📱 <b>Nuevo WhatsApp</b>",
        reference_doctype=message.reference_doctype,
        reference_name=message.reference_name,
        customer=customer,
    )


def send_email_alert(communication_name: str) -> None:
    """Envía el aviso de un correo recibido a los agentes asignados."""
    settings = get_telegram_settings()
    if not settings or not settings.telegram_notify_email:
        return

    comm = frappe.db.get_value(
        "Communication", communication_name,
        ["reference_doctype", "reference_name", "sender", "sender_full_name"],
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

    _notify_assignees(
        settings,
        flag="notify_email",
        title="✉️ <b>Nuevo correo</b>",
        reference_doctype=comm.reference_doctype,
        reference_name=comm.reference_name,
        customer=customer,
    )


# ---------------------------------------------------------------------------
# Botones de CORTEC Helpdesk Settings
# ---------------------------------------------------------------------------

@frappe.whitelist()
def detect_telegram_chats() -> list[dict]:
    """
    Devuelve los chats privados que escribieron al bot (p. ej. /start)
    en las últimas 24 h, según getUpdates. No confirma las
    actualizaciones, así que se puede volver a consultar.
    """
    frappe.only_for(MANAGER_ROLES)
    token = _get_token_or_throw()

    try:
        response = requests.get(
            TELEGRAM_API_URL.format(token=token, method="getUpdates"),
            timeout=REQUEST_TIMEOUT,
        )
        data = response.json()
    except Exception as e:
        frappe.throw(_("No se pudo consultar Telegram: {0}").format(_scrub(str(e), token)))

    if not data.get("ok"):
        frappe.throw(
            _("Telegram rechazó la consulta: {0}").format(data.get("description") or response.status_code)
        )

    chats = {}
    for update in data.get("result", []):
        msg = update.get("message") or update.get("edited_message") or {}
        chat = msg.get("chat") or {}
        if chat.get("type") != "private":
            continue
        name = " ".join(filter(None, [chat.get("first_name"), chat.get("last_name")]))
        chats[str(chat["id"])] = {
            "chat_id": str(chat["id"]),
            "name": name or str(chat["id"]),
            "username": chat.get("username"),
        }

    return list(chats.values())


@frappe.whitelist()
def send_telegram_test() -> list[dict]:
    """Envía un mensaje de prueba a cada agente de la tabla."""
    frappe.only_for(MANAGER_ROLES)
    token = _get_token_or_throw()
    settings = frappe.get_cached_doc("CORTEC Helpdesk Settings")

    results = []
    for row in settings.telegram_agents:
        text = (
            "✅ <b>Prueba de avisos CORTEC</b>\n"
            f"Este chat recibirá los avisos de {escape_html(row.user)}."
        )
        ok, error = _telegram_send(token, row.chat_id, text)
        results.append({"user": row.user, "ok": ok, "error": error})

    return results


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _notify_assignees(
    settings,
    flag: str,
    title: str,
    reference_doctype: str,
    reference_name: str,
    customer: str | None,
) -> None:
    """
    Envía el aviso a cada agente asignado al documento que tenga una fila
    en la tabla con ``flag`` activo, respetando el agrupamiento.
    """
    assignees = get_assignees(reference_doctype, reference_name)
    if not assignees:
        return

    rows = [
        row for row in settings.telegram_agents
        if row.user in assignees and row.get(flag)
    ]
    if not rows:
        return

    token = settings.get_password("telegram_bot_token", raise_exception=False)
    if not token:
        return

    link = build_link(reference_doctype, reference_name)
    lines = [
        title,
        escape_html(build_reference_label(reference_doctype, reference_name))
        + (f" — {escape_html(customer)}" if customer else ""),
        f'<a href="{escape_html(link)}">Abrir</a>',
    ]
    text = "\n".join(lines)

    throttle_seconds = cint(settings.telegram_throttle_minutes) * 60

    for row in rows:
        if throttle_seconds and not _acquire_throttle(
            row.user, reference_doctype, reference_name, throttle_seconds
        ):
            continue

        ok, error = _telegram_send(token, row.chat_id, text)
        if not ok:
            frappe.log_error(
                title="CORTEC Telegram: error enviando aviso",
                message=(
                    f"Agente {row.user} (chat {row.chat_id}), "
                    f"{reference_doctype} {reference_name}\nError: {error}"
                ),
            )


def _acquire_throttle(user: str, doctype: str, name: str, seconds: int) -> bool:
    """
    True si se debe avisar ahora. Usa SET NX con expiración para que dos
    trabajos simultáneos no envíen el mismo aviso.
    """
    cache = frappe.cache()
    key = cache.make_key(f"cortec_tg:{user}:{doctype}:{name}")
    return bool(cache.set(key, 1, ex=seconds, nx=True))


def _get_token_or_throw() -> str:
    settings = frappe.get_cached_doc("CORTEC Helpdesk Settings")
    token = settings.get_password("telegram_bot_token", raise_exception=False)
    if not token:
        frappe.throw(_("Configure el token del bot de Telegram y guarde antes de continuar."))
    return token


def _telegram_send(token: str, chat_id: str, text: str) -> tuple[bool, str | None]:
    """Envía un mensaje. Devuelve (ok, error) sin exponer el token."""
    try:
        response = requests.post(
            TELEGRAM_API_URL.format(token=token, method="sendMessage"),
            json={
                "chat_id": chat_id,
                "text": text,
                "parse_mode": "HTML",
                "disable_web_page_preview": True,
            },
            timeout=REQUEST_TIMEOUT,
        )
        data = response.json()
    except Exception as e:
        return False, _scrub(str(e), token)

    if not data.get("ok"):
        return False, data.get("description") or f"HTTP {response.status_code}"
    return True, None


def _scrub(text: str, token: str) -> str:
    """Quita el token de un mensaje de error (requests incluye la URL)."""
    return text.replace(token, "***") if token else text
