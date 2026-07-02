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
cortec_helpdesk.overrides.hd_ticket
====================================
Tres responsabilidades:

1. get_permission_query_conditions  — filtra la lista de tickets a nivel SQL.
2. has_permission                   — bloquea acceso directo por URL / API.
3. auto_assign_ticket               — asigna el ticket al agente responsable
                                      del contacto (vía el campo Custom Field
                                      "custom_account_manager" en Contact) y
                                      notifica al agente y supervisores.

Roles reconocidos
-----------------
  System Manager   → acceso total (rol estándar de Frappe)
  HD Manager       → acceso total (supervisor de helpdesk)
  HD Agent Lead    → acceso total (persona asignadora)
  HD Agent         → solo ve / accede a sus propios tickets asignados
"""

import frappe
from frappe import _


# ---------------------------------------------------------------------------
# Constantes
# ---------------------------------------------------------------------------

FULL_ACCESS_ROLES = {"System Manager", "HD Manager", "HD Agent Lead"}
AGENT_ROLE = "HD Agent"
HELPDESK_EMAIL = "soporte@tecnocr.net"


# ---------------------------------------------------------------------------
# Helpers de roles
# ---------------------------------------------------------------------------

def _user_has_full_access(user: str) -> bool:
    """Devuelve True si el usuario tiene algún rol de acceso total."""
    return bool(set(frappe.get_roles(user)) & FULL_ACCESS_ROLES)


def _is_agent(user: str) -> bool:
    """Devuelve True si el usuario tiene el rol de agente restringido."""
    return AGENT_ROLE in frappe.get_roles(user)


# ===========================================================================
# 1. FILTRO DE LISTA (permission_query_conditions)
# ===========================================================================

def get_permission_query_conditions(user: str = None) -> str:
    """
    Retorna una cláusula WHERE adicional que Frappe agrega a cada consulta
    sobre HD Ticket.

    - Administradores / managers / asignadores → sin restricción
    - Agentes HD → solo tickets donde ellos están en _assign
    - Cualquier otro → sin acceso
    """
    if not user:
        user = frappe.session.user

    if user == "Administrator":
        return ""

    if _user_has_full_access(user):
        return ""

    if _is_agent(user):
        escaped = frappe.db.escape(user, percent=False)
        return f"(`tabHD Ticket`.`_assign` LIKE '%{escaped}%')"

    return "1=0"


# ===========================================================================
# 2. ACCESO POR DOCUMENTO INDIVIDUAL (has_permission)
# ===========================================================================

def has_permission(doc, ptype: str = "read", user: str = None) -> bool:
    """
    Controla el acceso cuando alguien intenta abrir un ticket directamente
    (por URL, API o desde otro doctype vinculado).
    """
    if not user:
        user = frappe.session.user

    if user == "Administrator":
        return True

    if _user_has_full_access(user):
        return True

    if _is_agent(user):
        assigned_users = frappe.parse_json(doc.get("_assign") or "[]")
        if user in assigned_users:
            return True
        frappe.throw(
            _("No tiene permiso para ver este ticket. "
              "Solo puede acceder a tickets asignados a usted."),
            frappe.PermissionError,
        )

    return None


# ===========================================================================
# 3. ASIGNACIÓN AUTOMÁTICA AL CREAR TICKET
# ===========================================================================

def auto_assign_ticket(doc, method: str = None) -> None:
    """
    Se ejecuta en `after_insert` de HD Ticket.

    Flujo:
      email remitente → Contacto → Cliente → account_manager → asignar

    Si no se resuelve el agente, notifica a los HD Agent Lead.
    """
    agent = _resolve_agent_for_ticket(doc)

    if agent:
        _assign_ticket_to_agent(doc, agent)
    else:
        _notify_unassigned_ticket(doc)


# ---------------------------------------------------------------------------
# 3a. Resolución de agente
# ---------------------------------------------------------------------------

def _resolve_agent_for_ticket(doc) -> str | None:
    """
    Resuelve el agente responsable siguiendo la cadena:
      email remitente → Contacto → Contact.custom_account_manager
    """
    sender_email = _get_sender_email(doc)
    if not sender_email:
        return None

    contact_name = _find_contact_by_email(sender_email)
    if not contact_name:
        frappe.log_error(
            title="CORTEC Helpdesk: contacto no encontrado",
            message=(
                f"Ticket {doc.name}: no existe contacto para "
                f"{sender_email}. Requiere asignación manual."
            ),
        )
        return None

    account_manager = frappe.db.get_value(
        "Contact", contact_name, "custom_account_manager"
    )
    if not account_manager:
        frappe.log_error(
            title="CORTEC Helpdesk: account_manager no configurado",
            message=(
                f"Ticket {doc.name}: el contacto {contact_name} "
                f"no tiene account_manager asignado (campo 'Encargado de "
                f"Cuenta' en su ficha de Contacto)."
            ),
        )
        return None

    return account_manager


def _get_sender_email(doc) -> str | None:
    """Extrae el email del remitente del ticket."""
    sender = doc.get("raised_by") or doc.get("email") or ""
    if sender and "@" in sender:
        return sender.strip().lower()

    if doc.get("contact"):
        email = frappe.db.get_value("Contact", doc.contact, "email_id")
        if email:
            return email.strip().lower()

    return None


def _find_contact_by_email(email: str) -> str | None:
    """Busca un Contacto cuyo email_id coincida (tabla Contact Email)."""
    return frappe.db.get_value(
        "Contact Email", {"email_id": email}, "parent"
    ) or None


# ---------------------------------------------------------------------------
# 3b. Asignación y notificaciones
# ---------------------------------------------------------------------------

def _assign_ticket_to_agent(doc, agent: str) -> None:
    """
    Asigna el ticket al agente y envía notificaciones personalizadas
    tanto al agente como a los supervisores (HD Agent Lead).
    """
    try:
        from frappe.desk.form.assign_to import add as assign_to_add

        assign_to_add(
            {
                "doctype": "HD Ticket",
                "name": doc.name,
                "assign_to": [agent],
                "description": "Asignación automática por Encargado de Cuenta del contacto.",
                "notify": False,
            }
        )

        _send_agent_notification(doc, agent)
        _send_supervisor_notification(doc, agent)

        frappe.db.set_value(
            "HD Ticket", doc.name,
            "custom_auto_assigned", 1,
            update_modified=False,
        )

        frappe.logger("cortec_helpdesk").info(
            f"Ticket {doc.name} asignado automáticamente a {agent}"
        )

    except Exception as e:
        frappe.log_error(
            title="CORTEC Helpdesk: error en asignación automática",
            message=f"Ticket {doc.name} → agente {agent}\nError: {str(e)}",
        )


def _send_agent_notification(doc, agent: str) -> None:
    """Notifica al agente que se le asignó un ticket, desde soporte@."""
    subject = (
        f"[Ticket {doc.name}] Se le ha asignado: "
        f"{doc.get('subject', 'Sin asunto')}"
    )

    message = f"""
    <p>Hola,</p>
    <p>Se le ha asignado el siguiente ticket de soporte:</p>
    <table style="border-collapse: collapse; margin: 15px 0;">
        <tr>
            <td style="padding: 5px 15px 5px 0; font-weight: bold;">Ticket:</td>
            <td style="padding: 5px 0;">{doc.name}</td>
        </tr>
        <tr>
            <td style="padding: 5px 15px 5px 0; font-weight: bold;">Asunto:</td>
            <td style="padding: 5px 0;">{doc.get('subject', 'Sin asunto')}</td>
        </tr>
        <tr>
            <td style="padding: 5px 15px 5px 0; font-weight: bold;">Cliente:</td>
            <td style="padding: 5px 0;">{doc.get('raised_by', 'Desconocido')}</td>
        </tr>
        <tr>
            <td style="padding: 5px 15px 5px 0; font-weight: bold;">Fecha:</td>
            <td style="padding: 5px 0;">{frappe.format_date(doc.creation)}</td>
        </tr>
    </table>
    <p>Por favor revise y responda a la brevedad desde el panel de Helpdesk.</p>
    <p><a href="/helpdesk/tickets/{doc.name}">Abrir ticket en Helpdesk</a></p>
    <br>
    <p>— Sistema de Soporte CORTEC</p>
    """

    frappe.sendmail(
        recipients=[agent],
        sender=HELPDESK_EMAIL,
        subject=subject,
        message=message,
        reference_doctype="HD Ticket",
        reference_name=doc.name,
        now=True,
    )


def _send_supervisor_notification(doc, agent: str) -> None:
    """Notifica a los HD Agent Lead sobre la asignación automática."""
    agent_leads = frappe.get_all(
        "Has Role",
        filters={"role": "HD Agent Lead", "parenttype": "User"},
        pluck="parent",
    )

    if not agent_leads:
        return

    agent_leads = [u for u in agent_leads if u != agent]
    if not agent_leads:
        return

    agent_name = frappe.db.get_value("User", agent, "full_name") or agent

    subject = f"[Ticket {doc.name}] Asignado a {agent_name}"

    message = f"""
    <p>Hola,</p>
    <p>Se ha asignado automáticamente un ticket:</p>
    <table style="border-collapse: collapse; margin: 15px 0;">
        <tr>
            <td style="padding: 5px 15px 5px 0; font-weight: bold;">Ticket:</td>
            <td style="padding: 5px 0;">{doc.name}</td>
        </tr>
        <tr>
            <td style="padding: 5px 15px 5px 0; font-weight: bold;">Asunto:</td>
            <td style="padding: 5px 0;">{doc.get('subject', 'Sin asunto')}</td>
        </tr>
        <tr>
            <td style="padding: 5px 15px 5px 0; font-weight: bold;">Cliente:</td>
            <td style="padding: 5px 0;">{doc.get('raised_by', 'Desconocido')}</td>
        </tr>
        <tr>
            <td style="padding: 5px 15px 5px 0; font-weight: bold;">Asignado a:</td>
            <td style="padding: 5px 0;">{agent_name} ({agent})</td>
        </tr>
    </table>
    <p><a href="/helpdesk/tickets/{doc.name}">Ver ticket</a></p>
    <br>
    <p>— Sistema de Soporte CORTEC</p>
    """

    frappe.sendmail(
        recipients=agent_leads,
        sender=HELPDESK_EMAIL,
        subject=subject,
        message=message,
        reference_doctype="HD Ticket",
        reference_name=doc.name,
        now=True,
    )


def _notify_unassigned_ticket(doc) -> None:
    """
    Cuando no se resuelve el agente automáticamente, notifica a los
    HD Agent Lead para asignación manual.
    """
    try:
        agent_leads = frappe.get_all(
            "Has Role",
            filters={"role": "HD Agent Lead", "parenttype": "User"},
            pluck="parent",
        )

        if not agent_leads:
            return

        subject = f"[Ticket {doc.name}] Requiere asignación manual"

        message = f"""
        <p>Hola,</p>
        <p>El siguiente ticket <strong>no pudo asignarse automáticamente</strong>
        y requiere su intervención:</p>
        <table style="border-collapse: collapse; margin: 15px 0;">
            <tr>
                <td style="padding: 5px 15px 5px 0; font-weight: bold;">Ticket:</td>
                <td style="padding: 5px 0;">{doc.name}</td>
            </tr>
            <tr>
                <td style="padding: 5px 15px 5px 0; font-weight: bold;">Asunto:</td>
                <td style="padding: 5px 0;">{doc.get('subject', 'Sin asunto')}</td>
            </tr>
            <tr>
                <td style="padding: 5px 15px 5px 0; font-weight: bold;">Remitente:</td>
                <td style="padding: 5px 0;">{doc.get('raised_by', 'Desconocido')}</td>
            </tr>
            <tr>
                <td style="padding: 5px 15px 5px 0; font-weight: bold;">Motivo:</td>
                <td style="padding: 5px 0;">No se encontró Encargado de
                    Cuenta para este contacto. Revise Error Log para detalles.</td>
            </tr>
        </table>
        <p><a href="/helpdesk/tickets/{doc.name}">Asignar ticket manualmente</a></p>
        <br>
        <p>— Sistema de Soporte CORTEC</p>
        """

        frappe.sendmail(
            recipients=agent_leads,
            sender=HELPDESK_EMAIL,
            subject=subject,
            message=message,
            reference_doctype="HD Ticket",
            reference_name=doc.name,
            now=True,
        )

    except Exception as e:
        frappe.log_error(
            title="CORTEC Helpdesk: error notificando ticket sin asignar",
            message=str(e),
        )
