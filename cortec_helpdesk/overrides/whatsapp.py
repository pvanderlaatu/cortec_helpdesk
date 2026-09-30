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
cortec_helpdesk.overrides.whatsapp
===================================
Complementa el hook nativo de Frappe CRM sobre WhatsApp Message
(crm.api.whatsapp.validate/on_update), que ya vincula un mensaje a un
Contact/CRM Lead/CRM Deal existente por número de teléfono, pero que no
genera nada visible cuando no encuentra ningún match, o cuando el único
match es un Contact suelto o un Lead/Deal ya cerrado.

route_unclaimed_message  — en after_insert de un WhatsApp Message
                            Incoming: reutiliza el Lead/Deal en
                            seguimiento del mismo número si existe, y
                            solo crea un CRM Lead nuevo cuando no hay
                            ninguno. Después notifica al agente asignado.

La asignación del Lead nuevo NO se reimplementa aquí: se deja que la
misma estrategia de asignación que Frappe/CRM ya aplican a los Leads
creados desde correo (p. ej. una Assignment Rule) actúe sobre el
insert() normal del Lead.
"""

import frappe


# ---------------------------------------------------------------------------
# Constantes
# ---------------------------------------------------------------------------

# Tipos de status de CRM Lead Status / CRM Deal Status que cuentan como
# cerrado. Los demás ("Open", "Ongoing", "On Hold") son seguimiento vivo:
# un WhatsApp nuevo va al Lead/Deal existente, NO crea otro.
CLOSED_STATUS_TYPES = {"Won", "Lost"}

# Últimos dígitos que se comparan al buscar un Lead/Deal por teléfono.
# Evita que "+50661591066", "50661591066" y "61591066" se traten como
# números distintos.
PHONE_MATCH_DIGITS = 8


# ---------------------------------------------------------------------------
# Hook principal
# ---------------------------------------------------------------------------

def route_unclaimed_message(doc, method: str = None) -> None:
    """
    Hook ``after_insert`` en WhatsApp Message.

    Solo actúa sobre mensajes Incoming, y en este orden:

      1. Si crm.api.whatsapp.validate ya vinculó el mensaje a un CRM
         Lead o CRM Deal en seguimiento, no hace nada (CRM ya notifica
         vía su propio on_update).
      2. Si no, busca por teléfono un Lead/Deal en seguimiento y vincula
         el mensaje a él. Cubre los casos en que el match de CRM falla
         por el formato del número.
      3. Solo si no hay nada, crea un CRM Lead nuevo.

    Así, un cliente que ya tiene un Lead en "Contacted" o "Nurture" no
    genera un Lead nuevo con cada mensaje.

    Defensivo: un error aquí nunca debe interrumpir la recepción del
    mensaje de WhatsApp (corre en el flujo de un webhook entrante).
    """
    try:
        # Histórico que llega por la migración desde Bitrix24: ni avisos
        # ni Leads nuevos por mensajes de hace años.
        if frappe.flags.get("in_bitrix24_migration"):
            return
        if doc.type != "Incoming":
            return

        phone_number = (doc.get("from") or "").strip()
        if not phone_number:
            frappe.log_error(
                title="CORTEC WhatsApp: mensaje entrante sin número",
                message=f"WhatsApp Message {doc.name} no tiene 'from'.",
            )
            return

        if _has_active_reference(doc):
            # CRM ya lo tiene vinculado a un Lead/Deal en seguimiento;
            # su propio notify_agent (on_update) se encarga de notificar.
            return

        existing = _find_active_reference_by_phone(phone_number)
        if existing:
            doctype, name = existing
            _link_message_to(doc, doctype, name)
            frappe.logger("cortec_helpdesk").info(
                f"WhatsApp Message {doc.name} → {doctype} {name} existente "
                f"({phone_number})"
            )
        else:
            name = _create_lead_from_message(doc, phone_number)
            if not name:
                return
            doctype = "CRM Lead"
            _link_message_to(doc, doctype, name)
            frappe.logger("cortec_helpdesk").info(
                f"WhatsApp Message {doc.name} → CRM Lead {name} nuevo "
                f"({phone_number})"
            )

        from crm.api.whatsapp import notify_agent

        notify_agent(doc)

    except Exception as e:
        frappe.log_error(
            title="CORTEC WhatsApp: error enrutando mensaje entrante",
            message=f"WhatsApp Message {doc.name}\nError: {str(e)}",
        )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _has_active_reference(doc) -> bool:
    """
    True si doc.reference_doctype/reference_name (ya fijados por
    crm.api.whatsapp.validate) apuntan a un CRM Lead o CRM Deal en
    seguimiento, es decir, cuyo status NO es de tipo "Won" ni "Lost".

    Un Contact suelto cuenta como "no activo": no hay Lead/Deal donde
    aparezca la conversación.

    Antes solo se aceptaba el tipo "Open", así que un Lead en
    "Contacted", "Nurture" o "Qualified" (tipo "Ongoing") generaba un
    Lead nuevo con cada mensaje del mismo cliente.
    """
    doctype = doc.get("reference_doctype")
    name = doc.get("reference_name")

    if not doctype or not name or doctype not in ("CRM Lead", "CRM Deal"):
        return False

    if not frappe.db.exists(doctype, name):
        return False

    status = frappe.db.get_value(doctype, name, "status")
    if not status:
        # Sin status no se puede saber: se reutiliza el vínculo igual,
        # que es preferible a duplicar.
        return True

    status_doctype = "CRM Lead Status" if doctype == "CRM Lead" else "CRM Deal Status"
    try:
        status_type = frappe.get_cached_value(status_doctype, status, "type")
    except Exception:
        frappe.log_error(
            title="CORTEC WhatsApp: no se pudo leer 'type' de status",
            message=f"{status_doctype} / {status} (WhatsApp Message {doc.name})",
        )
        # Ante la duda, NO crear otro Lead.
        return True

    return status_type not in CLOSED_STATUS_TYPES


def _find_active_reference_by_phone(phone_number: str) -> tuple[str, str] | None:
    """
    Busca un CRM Deal o CRM Lead en seguimiento cuyo teléfono coincida en
    los últimos PHONE_MATCH_DIGITS dígitos. Red de seguridad para cuando
    el match de crm.api.whatsapp falla por formato ("+506…" contra
    "506…"), que es la otra vía por la que se duplicaban Leads.

    Devuelve (doctype, name) del más reciente, o None.
    """
    digits = "".join(c for c in (phone_number or "") if c.isdigit())
    if len(digits) < PHONE_MATCH_DIGITS:
        return None

    tail = digits[-PHONE_MATCH_DIGITS:]

    # Primero Deals: si el cliente ya avanzó a negociación, la
    # conversación pertenece ahí.
    for doctype in ("CRM Deal", "CRM Lead"):
        meta = frappe.get_meta(doctype)
        phone_fields = [f for f in ("mobile_no", "phone") if meta.has_field(f)]
        if not phone_fields:
            continue

        rows = frappe.get_all(
            doctype,
            or_filters=[[field, "like", f"%{tail}%"] for field in phone_fields],
            fields=["name", "status"],
            order_by="modified desc",
            limit=20,
        )
        status_doctype = "CRM Lead Status" if doctype == "CRM Lead" else "CRM Deal Status"
        for row in rows:
            if not row.status:
                return doctype, row.name
            try:
                status_type = frappe.get_cached_value(status_doctype, row.status, "type")
            except Exception:
                return doctype, row.name
            if status_type not in CLOSED_STATUS_TYPES:
                return doctype, row.name

    return None


def _create_lead_from_message(doc, phone_number: str) -> str | None:
    """
    Crea un CRM Lead nuevo a partir del mensaje entrante.

    Si el match previo de crm.api.whatsapp.validate fue a un Contact
    suelto (reference_doctype == "Contact"), usa los datos de ese
    Contact. Si no hubo match, usa el número + profile_name del mensaje.

    No fija lead_owner ni reparte el Lead a mano: se deja que la
    estrategia de asignación ya configurada para Leads de correo actúe
    sobre este insert() normal.
    """
    first_name = None
    mobile_no = phone_number
    email_id = None

    if doc.get("reference_doctype") == "Contact" and doc.get("reference_name"):
        contact = frappe.db.get_value(
            "Contact", doc.reference_name,
            ["first_name", "mobile_no", "email_id"],
            as_dict=True,
        )
        if contact:
            first_name = contact.first_name
            mobile_no = contact.mobile_no or phone_number
            email_id = contact.email_id

    if not first_name:
        first_name = doc.get("profile_name") or phone_number

    lead = frappe.new_doc("CRM Lead")
    lead.first_name = first_name
    lead.mobile_no = mobile_no
    if email_id:
        lead.email = email_id

    try:
        lead.insert(ignore_permissions=True)
    except Exception as e:
        frappe.log_error(
            title="CORTEC WhatsApp: error creando CRM Lead",
            message=(
                f"WhatsApp Message {doc.name}, número {phone_number}\n"
                f"Error: {str(e)}"
            ),
        )
        return None

    frappe.logger("cortec_helpdesk").info(
        f"CRM Lead {lead.name} creado desde WhatsApp Message {doc.name} "
        f"({phone_number})"
    )
    return lead.name


def _link_message_to(doc, doctype: str, name: str) -> None:
    """
    Vincula el WhatsApp Message al Lead o Deal resuelto.

    Usa frappe.db.set_value (no doc.save()) para no re-disparar
    validate/on_update de WhatsApp Message de forma anidada dentro de su
    propio after_insert. Por eso route_unclaimed_message llama a
    notify_agent manualmente después de esto.
    """
    frappe.db.set_value(
        "WhatsApp Message", doc.name,
        {
            "reference_doctype": doctype,
            "reference_name": name,
        },
        update_modified=False,
    )
    # Refleja el cambio también en el objeto en memoria, ya que
    # notify_agent(doc) se llama sobre este mismo doc a continuación.
    doc.reference_doctype = doctype
    doc.reference_name = name
