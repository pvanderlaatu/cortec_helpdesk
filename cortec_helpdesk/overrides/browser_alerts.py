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
cortec_helpdesk.overrides.browser_alerts
=========================================
Alerta audible en el navegador mientras el agente tiene /crm o
/helpdesk abierto. Complementa (no reemplaza) los avisos por Telegram.

AlertsTemplatePage — page_renderer que inserta public/js/browser_alerts.js
                     en las páginas de las SPA de CRM y Helpdesk, sin
                     modificar el código de esas apps.
get_new_alerts     — endpoint que el script consulta periódicamente:
                     WhatsApp entrantes (CRM Notification) y correos
                     recibidos en documentos asignados al usuario.

Mismo criterio y misma privacidad que Telegram: solo documento, nombre
del cliente y enlace; nunca el texto del mensaje ni el asunto.
"""

import frappe
from frappe.utils import add_to_date, cint, escape_html, get_datetime, now_datetime
from frappe.website.page_renderers.template_page import TemplatePage

from cortec_helpdesk import __version__
from cortec_helpdesk.cortec_helpdesk.doctype.cortec_helpdesk_settings.cortec_helpdesk_settings import (
    MIN_BROWSER_POLL_SECONDS,
    get_browser_alert_settings,
)
from cortec_helpdesk.overrides.alert_utils import (
    REFERENCE_DOCTYPES,
    build_link,
    build_reference_label,
    get_assignees,
    get_display_name,
    is_internal_sender,
)


# Rutas (ya resueltas por los website_route_rules de cada app) donde se
# inserta el script.
ALERT_ROUTES = ("crm", "helpdesk")

SCRIPT_PATH = "/assets/cortec_helpdesk/js/browser_alerts.js"

# Una consulta nunca mira más atrás que esto, para que al volver tras
# horas sin la página abierta no suene una ráfaga de avisos viejos.
MAX_LOOKBACK_MINUTES = 10
MAX_ALERTS = 20


# ---------------------------------------------------------------------------
# Inyección del script
# ---------------------------------------------------------------------------

class AlertsTemplatePage(TemplatePage):
    """
    Igual que TemplatePage para /crm y /helpdesk, pero añade el script de
    alertas antes de </body> si están habilitadas en Settings.
    """

    def can_render(self):
        return self.path in ALERT_ROUTES and super().can_render()

    def render(self):
        response = super().render()
        try:
            settings = get_browser_alert_settings()
            if not settings or frappe.session.user == "Guest":
                return response
            if response.mimetype != "text/html":
                return response

            html = response.get_data(as_text=True)
            if "</body>" not in html:
                return response

            poll = max(cint(settings.browser_alert_poll_seconds), MIN_BROWSER_POLL_SECONDS)
            tag = (
                f'<script src="{SCRIPT_PATH}?v={escape_html(__version__)}" '
                f'data-poll="{poll}" defer></script>'
            )
            response.set_data(html.replace("</body>", f"{tag}\n</body>", 1))
        except Exception as e:
            # Nunca romper la carga de /crm o /helpdesk por esto.
            frappe.log_error(
                title="CORTEC Alertas navegador: error insertando script",
                message=f"Ruta {self.path}\nError: {str(e)}",
            )
        return response


# ---------------------------------------------------------------------------
# Endpoint
# ---------------------------------------------------------------------------

@frappe.whitelist()
def get_new_alerts(since: str | None = None) -> dict:
    """
    Alertas nuevas del usuario de la sesión desde ``since`` (hora del
    servidor devuelta por la consulta anterior).

    Sin ``since`` (primera consulta) no devuelve alertas: solo la hora
    actual, para no hacer sonar mensajes viejos.
    """
    user = frappe.session.user
    now = now_datetime()
    result = {"enabled": False, "user": user, "now": str(now), "alerts": []}

    settings = get_browser_alert_settings()
    if not settings or user == "Guest":
        return result
    result["enabled"] = True

    since_dt = _parse_since(since, now)
    if not since_dt:
        return result

    alerts = []
    if settings.browser_alert_whatsapp:
        alerts.extend(_whatsapp_alerts(user, since_dt, now))
    if settings.browser_alert_email:
        alerts.extend(_email_alerts(user, since_dt, now))

    result["alerts"] = alerts[:MAX_ALERTS]
    return result


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _parse_since(since: str | None, now):
    """``since`` como datetime, acotado a MAX_LOOKBACK_MINUTES; None si falta o no es válido."""
    if not since:
        return None
    try:
        since_dt = get_datetime(since)
    except Exception:
        return None
    if not since_dt:
        return None
    return max(since_dt, add_to_date(now, minutes=-MAX_LOOKBACK_MINUTES))


def _whatsapp_alerts(user: str, since_dt, now) -> list[dict]:
    """
    CRM Notification de tipo WhatsApp, no leídas, para el usuario. Las
    crea crm.api.whatsapp.notify_agent (también desde
    route_unclaimed_message) solo para los agentes asignados.
    """
    notifications = frappe.get_all(
        "CRM Notification",
        filters=[
            ["to_user", "=", user],
            ["type", "=", "WhatsApp"],
            ["read", "=", 0],
            ["creation", ">", since_dt],
            ["creation", "<=", now],
        ],
        fields=[
            "reference_doctype", "reference_name",
            "notification_type_doctype", "notification_type_doc",
        ],
        order_by="creation asc",
        limit=MAX_ALERTS,
    )

    alerts, seen = [], set()
    for n in notifications:
        doctype, name, customer = _resolve_whatsapp_reference(n)
        if not doctype or (doctype, name) in seen:
            continue
        seen.add((doctype, name))
        alerts.append(
            _build_alert(
                "whatsapp", "Nuevo WhatsApp", doctype, name,
                customer or get_display_name(doctype, name),
            )
        )
    return alerts


def _resolve_whatsapp_reference(notification) -> tuple[str | None, str | None, str | None]:
    """(doctype, name, nombre del perfil) del Lead/Deal de una notificación de WhatsApp."""
    doctype = notification.notification_type_doctype
    name = notification.notification_type_doc
    customer = None

    if notification.reference_doctype == "WhatsApp Message" and notification.reference_name:
        meta = frappe.get_meta("WhatsApp Message")
        fields = ["reference_doctype", "reference_name"]
        if meta.has_field("profile_name"):
            fields.append("profile_name")
        message = frappe.db.get_value(
            "WhatsApp Message", notification.reference_name, fields, as_dict=True
        )
        if message:
            customer = message.get("profile_name")
            if doctype not in REFERENCE_DOCTYPES:
                doctype, name = message.reference_doctype, message.reference_name

    if doctype not in REFERENCE_DOCTYPES or not name:
        return None, None, None
    return doctype, name, customer


def _email_alerts(user: str, since_dt, now) -> list[dict]:
    """
    Correos recibidos en CRM Lead / CRM Deal / HD Ticket asignados al
    usuario, excluyendo los enviados por agentes. Un aviso por documento.
    """
    alerts, seen = [], set()
    assign_pattern = f'%"{user}"%'

    for doctype in REFERENCE_DOCTYPES:
        if not frappe.db.table_exists(doctype):
            continue

        rows = frappe.db.sql(
            f"""
            SELECT c.reference_name, c.sender, c.sender_full_name
            FROM `tabCommunication` c
            JOIN `tab{doctype}` d ON d.name = c.reference_name
            WHERE c.communication_medium = 'Email'
              AND c.sent_or_received = 'Received'
              AND c.reference_doctype = %(doctype)s
              AND c.creation > %(since)s
              AND c.creation <= %(now)s
              AND d._assign LIKE %(assign)s
            ORDER BY c.creation ASC
            LIMIT {MAX_ALERTS}
            """,
            {"doctype": doctype, "since": since_dt, "now": now, "assign": assign_pattern},
            as_dict=True,
        )

        for row in rows:
            key = (doctype, row.reference_name)
            if key in seen:
                continue
            # LIKE puede dar falsos positivos con '_' en el usuario: confirmar.
            if user not in get_assignees(doctype, row.reference_name):
                continue
            if is_internal_sender(row.sender):
                continue
            seen.add(key)
            customer = get_display_name(doctype, row.reference_name) or row.sender_full_name
            alerts.append(
                _build_alert("email", "Nuevo correo", doctype, row.reference_name, customer)
            )

    return alerts


def _build_alert(kind: str, title: str, doctype: str, name: str, customer: str | None) -> dict:
    body = build_reference_label(doctype, name)
    if customer:
        body += f" — {customer}"
    return {
        "kind": kind,
        "title": title,
        "body": body,
        "link": build_link(doctype, name, absolute=False),
        "tag": f"{doctype}:{name}",
    }
