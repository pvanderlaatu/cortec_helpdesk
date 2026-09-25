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
cortec_helpdesk.overrides.raven
================================
Canal de avisos por Raven: un mensaje directo de un bot al agente
asignado. A diferencia de Telegram, Raven corre en el mismo servidor,
así que el aviso NO sale de la infraestructura de CORTEC. Por eso aquí
sí se puede incluir el contenido del mensaje, si se activa a propósito
("Incluir el contenido del mensaje" en Settings).

El destinatario es el propio usuario de Frappe: no hay nada que
vincular, basta con que el agente tenga un Raven User habilitado.

El despachador (overrides/alerts.py) llama a is_enabled_for(kind) y
notify(event, assignees).

Nota: la app raven es opcional. Todo aquí comprueba primero que esté
instalada y habilitada.
"""

import frappe
from frappe import _
from frappe.utils import cint

from cortec_helpdesk.cortec_helpdesk.doctype.cortec_helpdesk_settings.cortec_helpdesk_settings import (
    get_raven_settings,
    is_raven_installed,
)
from cortec_helpdesk.overrides.alert_utils import (
    acquire_throttle,
    build_link,
    build_reference_label,
)


MANAGER_ROLES = ("System Manager", "Agent Manager", "HD Manager")

# Longitud máxima del contenido incluido, cuando está activado.
MAX_TEXT_LENGTH = 200

KIND_CONFIG = {
    "whatsapp": {"setting": "raven_notify_whatsapp", "title": "📱 **Nuevo WhatsApp**"},
    "email": {"setting": "raven_notify_email", "title": "✉️ **Nuevo correo**"},
}


# ---------------------------------------------------------------------------
# Interfaz del canal
# ---------------------------------------------------------------------------

def is_enabled_for(kind: str) -> bool:
    if not is_raven_installed():
        return False
    settings = get_raven_settings()
    config = KIND_CONFIG.get(kind)
    return bool(settings and config and settings.get(config["setting"]))


def notify(event: dict, assignees: list[str]) -> None:
    """Manda un mensaje directo del bot a cada agente asignado con Raven."""
    settings = get_raven_settings()
    config = KIND_CONFIG.get(event["kind"])
    if not settings or not config or not settings.raven_bot:
        return

    recipients = [user for user in assignees if _has_raven_user(user)]
    if not recipients:
        return

    text = _build_text(settings, config, event)
    throttle_seconds = cint(settings.raven_throttle_minutes) * 60

    for user in recipients:
        if throttle_seconds and not acquire_throttle(
            "raven", user, event["doctype"], event["name"], throttle_seconds
        ):
            continue

        try:
            _send_direct_message(settings.raven_bot, user, text, event)
        except Exception as e:
            frappe.log_error(
                title="CORTEC Raven: error enviando aviso",
                message=(
                    f"Agente {user}, {event['doctype']} {event['name']}\n"
                    f"Error: {str(e)}"
                ),
            )


# ---------------------------------------------------------------------------
# Botón de CORTEC Helpdesk Settings
# ---------------------------------------------------------------------------

@frappe.whitelist()
def send_raven_test() -> dict:
    """Envía un mensaje de prueba del bot a quien pulsa el botón."""
    frappe.only_for(MANAGER_ROLES)

    if not is_raven_installed():
        frappe.throw(_("La app Raven no está instalada en este sitio."))

    settings = frappe.get_cached_doc("CORTEC Helpdesk Settings")
    if not settings.raven_bot:
        frappe.throw(_("Elija el bot de Raven y guarde antes de continuar."))

    user = frappe.session.user
    if not _has_raven_user(user):
        frappe.throw(
            _("Su usuario no tiene un Raven User habilitado: agréguese a Raven primero.")
        )

    _send_direct_message(
        settings.raven_bot, user,
        "✅ **Prueba de avisos CORTEC**\nAquí recibirá los avisos de WhatsApp y correos.",
        None,
    )
    return {"ok": True, "user": user}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _has_raven_user(user: str) -> bool:
    return bool(frappe.db.exists("Raven User", {"user": user, "enabled": 1}))


def _build_text(settings, config: dict, event: dict) -> str:
    link = build_link(event["doctype"], event["name"])
    customer = event.get("customer")

    header = config["title"]
    reference = build_reference_label(event["doctype"], event["name"])
    if customer:
        reference += f" — {customer}"

    lines = [f"{header}\n{reference}"]

    if settings.raven_include_message and event.get("text"):
        text = str(event["text"]).strip()
        if len(text) > MAX_TEXT_LENGTH:
            text = text[:MAX_TEXT_LENGTH] + "…"
        lines.append(f"> {text}")

    lines.append(f"[Abrir]({link})")
    return "\n\n".join(lines)


def _send_direct_message(bot_name: str, user: str, text: str, event: dict | None) -> None:
    """
    Mensaje directo del bot al usuario. Raven crea el canal DM si no
    existe. link_doctype/link_document hacen que Raven muestre el
    documento junto al mensaje.
    """
    bot = frappe.get_doc("Raven Bot", bot_name)
    kwargs = {"user_id": user, "text": text, "markdown": True}
    if event:
        kwargs["link_doctype"] = event["doctype"]
        kwargs["link_document"] = event["name"]
    bot.send_direct_message(**kwargs)
