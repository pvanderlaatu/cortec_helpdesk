# Copyright (C) 2026 Corporación de Tecnología CORTEC S.R.L.
# SPDX-License-Identifier: AGPL-3.0-or-later
from __future__ import annotations

"""
cortec_helpdesk.overrides.raven_channel
========================================
Alertas de Helpdesk para todo el equipo, publicadas por el bot de Raven
en un canal (p. ej. "Alertas CORTEC" en raven-alertas-cortec). A
diferencia de raven.py, que manda mensajes directos al agente asignado,
aquí el destino es un canal compartido.

Eventos: ticket nuevo, ticket urgente (creado o cambiado a Urgent) y SLA
vencido. Un error aquí nunca debe romper el guardado del ticket: los
hooks solo encolan y todo va en try/except.
"""

import frappe
from frappe.utils import add_to_date, now_datetime

from cortec_helpdesk.cortec_helpdesk.doctype.cortec_helpdesk_settings.cortec_helpdesk_settings import (
    get_raven_settings,
)
from cortec_helpdesk.overrides.alert_utils import build_link, get_assignees

URGENT = "Urgent"
CLOSED_STATUSES = ("Resolved", "Closed")
# El SLA solo se avisa si cambió recientemente y una sola vez por ticket.
SLA_WINDOW_HOURS = 24
SLA_DEDUPE_SECONDS = 30 * 24 * 3600


def _settings(flag: str):
    """Settings si Raven, el canal y la casilla `flag` están activos; si no, None."""
    settings = get_raven_settings()
    if not settings or not settings.raven_bot or not settings.get("raven_alert_channel"):
        return None
    return settings if settings.get(flag) else None


# ---------------------------------------------------------------------------
# Hooks de HD Ticket
# ---------------------------------------------------------------------------

def on_ticket_insert(doc, method: str = None) -> None:
    """after_insert: ticket nuevo (con marca de urgente si lo es)."""
    try:
        if frappe.flags.get("in_bitrix24_migration"):
            return
        urgent = doc.get("priority") == URGENT
        if urgent and _settings("raven_alert_urgent"):
            kind = "urgent"
        elif _settings("raven_alert_new_ticket"):
            kind = "new"
        else:
            return
        _enqueue(kind, doc.name)
    except Exception as e:
        frappe.log_error(
            title="CORTEC Raven canal: error encolando ticket nuevo",
            message=f"HD Ticket {doc.name}\nError: {e}",
        )


def on_ticket_update(doc, method: str = None) -> None:
    """on_update: ticket cambiado a Urgent."""
    try:
        if frappe.flags.get("in_bitrix24_migration") or doc.flags.in_insert:
            return
        if doc.get("priority") != URGENT or not doc.has_value_changed("priority"):
            return
        if not _settings("raven_alert_urgent"):
            return
        _enqueue("urgent", doc.name)
    except Exception as e:
        frappe.log_error(
            title="CORTEC Raven canal: error encolando ticket urgente",
            message=f"HD Ticket {doc.name}\nError: {e}",
        )


def _enqueue(kind: str, ticket: str) -> None:
    frappe.enqueue(
        "cortec_helpdesk.overrides.raven_channel.dispatch",
        queue="short",
        kind=kind,
        ticket=ticket,
        enqueue_after_commit=True,
    )


# ---------------------------------------------------------------------------
# Trabajos en cola y programados
# ---------------------------------------------------------------------------

def dispatch(kind: str, ticket: str) -> None:
    """Publica en el canal el aviso `kind` (new | urgent | sla) del ticket."""
    flag = {
        "new": "raven_alert_new_ticket",
        "urgent": "raven_alert_urgent",
        "sla": "raven_alert_sla",
    }[kind]
    settings = _settings(flag)
    if not settings or not frappe.db.exists("HD Ticket", ticket):
        return

    try:
        _post(settings, build_text(kind, ticket))
    except Exception as e:
        frappe.log_error(
            title="CORTEC Raven canal: error publicando alerta",
            message=f"{kind} HD Ticket {ticket}\nError: {e}",
        )


def check_sla_breaches() -> None:
    """
    Cron cada 5 minutos: tickets abiertos con SLA Failed. Helpdesk cambia
    agreement_status desde su propio job, sin garantizar eventos del
    documento, por eso se consulta en vez de engancharse a on_update.
    """
    if not _settings("raven_alert_sla"):
        return

    tickets = frappe.get_all(
        "HD Ticket",
        filters={
            "agreement_status": "Failed",
            "status": ["not in", CLOSED_STATUSES],
            "modified": [">=", add_to_date(now_datetime(), hours=-SLA_WINDOW_HOURS)],
        },
        pluck="name",
        limit=50,
    )
    cache = frappe.cache()
    for ticket in tickets:
        key = cache.make_key(f"cortec_raven_channel:sla:{ticket}")
        if cache.set(key, 1, ex=SLA_DEDUPE_SECONDS, nx=True):
            dispatch("sla", ticket)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

TITLES = {
    "new": "🎫 **Nuevo ticket**",
    "urgent": "🚨 **Ticket URGENTE**",
    "sla": "⏰ **SLA vencido**",
}


def build_text(kind: str, ticket: str) -> str:
    t = frappe.db.get_value(
        "HD Ticket", ticket,
        ["subject", "customer", "raised_by", "priority"],
        as_dict=True,
    ) or {}
    who = t.get("customer") or t.get("raised_by") or "Desconocido"
    assignees = get_assignees("HD Ticket", ticket)
    assigned = ", ".join(
        frappe.db.get_value("User", u, "full_name") or u for u in assignees
    ) or "Sin asignar"

    lines = [
        f"{TITLES[kind]}\nTicket #{ticket} — {t.get('subject') or 'Sin asunto'}",
        f"Cliente: {who}\nPrioridad: {t.get('priority') or '-'}\nAsignado a: {assigned}",
        f"[Abrir ticket]({build_link('HD Ticket', ticket)})",
    ]
    return "\n\n".join(lines)


def _post(settings, text: str) -> None:
    bot = frappe.get_doc("Raven Bot", settings.raven_bot)
    bot.send_message(channel_id=settings.raven_alert_channel.strip(), text=text, markdown=True)
