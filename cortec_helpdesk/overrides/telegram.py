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
Canal de avisos por Telegram: un mensaje del bot al móvil del agente
asignado, que suena aunque el teléfono esté bloqueado.

El despachador (overrides/alerts.py) llama a:
  is_enabled_for(kind) — ¿está habilitado este canal para "whatsapp"
                         o para "email"?
  notify(event, assignees) — envía el aviso a los agentes vinculados.

Privacidad: el aviso pasa por los servidores de Telegram, así que lleva
solo el documento, el nombre del cliente y el enlace; nunca el texto del
mensaje ni el asunto del correo.
"""

import frappe
import requests
from frappe import _
from frappe.utils import cint, escape_html

from cortec_helpdesk.cortec_helpdesk.doctype.cortec_helpdesk_settings.cortec_helpdesk_settings import (
    get_telegram_settings,
)
from cortec_helpdesk.overrides.alert_utils import (
    acquire_throttle,
    build_link,
    build_reference_label,
)


TELEGRAM_API_URL = "https://api.telegram.org/bot{token}/{method}"
REQUEST_TIMEOUT = 10

# Quién puede usar los botones de Telegram en Settings. "Agent Manager"
# es el rol de supervisor de Frappe Helpdesk.
MANAGER_ROLES = ("System Manager", "Agent Manager", "HD Manager")

# Por tipo de evento: interruptor global, campo por agente y título.
KIND_CONFIG = {
    "whatsapp": {
        "setting": "telegram_notify_whatsapp",
        "row_flag": "notify_whatsapp",
        "title": "📱 <b>Nuevo WhatsApp</b>",
    },
    "email": {
        "setting": "telegram_notify_email",
        "row_flag": "notify_email",
        "title": "✉️ <b>Nuevo correo</b>",
    },
}


# ---------------------------------------------------------------------------
# Interfaz del canal
# ---------------------------------------------------------------------------

def is_enabled_for(kind: str) -> bool:
    settings = get_telegram_settings()
    config = KIND_CONFIG.get(kind)
    return bool(settings and config and settings.get(config["setting"]))


def notify(event: dict, assignees: list[str]) -> None:
    """
    Envía el aviso a cada agente asignado que tenga una fila en la tabla
    con el tipo de evento activo, respetando el agrupamiento de ráfagas.
    """
    settings = get_telegram_settings()
    config = KIND_CONFIG.get(event["kind"])
    if not settings or not config:
        return

    rows = [
        row for row in settings.telegram_agents
        if row.user in assignees and row.get(config["row_flag"])
    ]
    if not rows:
        return

    token = settings.get_password("telegram_bot_token", raise_exception=False)
    if not token:
        return

    link = build_link(event["doctype"], event["name"])
    customer = event.get("customer")
    lines = [
        config["title"],
        escape_html(build_reference_label(event["doctype"], event["name"]))
        + (f" — {escape_html(customer)}" if customer else ""),
        f'<a href="{escape_html(link)}">Abrir</a>',
    ]
    text = "\n".join(lines)

    throttle_seconds = cint(settings.telegram_throttle_minutes) * 60

    for row in rows:
        if throttle_seconds and not acquire_throttle(
            "telegram", row.user, event["doctype"], event["name"], throttle_seconds
        ):
            continue

        ok, error = _telegram_send(token, row.chat_id, text)
        if not ok:
            frappe.log_error(
                title="CORTEC Telegram: error enviando aviso",
                message=(
                    f"Agente {row.user} (chat {row.chat_id}), "
                    f"{event['doctype']} {event['name']}\nError: {error}"
                ),
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
