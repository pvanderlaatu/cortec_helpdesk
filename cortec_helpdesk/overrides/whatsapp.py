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
                            Incoming: si no quedó vinculado a un Lead/Deal
                            con status "Open", crea un CRM Lead nuevo,
                            vincula el mensaje a él y notifica al agente
                            que haya quedado asignado.

La asignación del Lead nuevo NO se reimplementa aquí: se deja que la
misma estrategia de asignación que Frappe/CRM ya aplican a los Leads
creados desde correo (p. ej. una Assignment Rule) actúe sobre el
insert() normal del Lead.
"""

import frappe


# ---------------------------------------------------------------------------
# Hook principal
# ---------------------------------------------------------------------------

def route_unclaimed_message(doc, method: str = None) -> None:
    """
    Hook ``after_insert`` en WhatsApp Message.

    Solo actúa sobre mensajes Incoming. Si crm.api.whatsapp.validate ya
    vinculó el mensaje a un CRM Lead o CRM Deal con status de tipo
    "Open", no hace nada (CRM ya notifica nativamente vía su propio
    on_update). En cualquier otro caso (sin vínculo, vínculo a un
    Contact suelto, o vínculo a un Lead/Deal cerrado) crea un CRM Lead
    nuevo y vincula el mensaje a él.

    Defensivo: un error aquí nunca debe interrumpir la recepción del
    mensaje de WhatsApp (corre en el flujo de un webhook entrante).
    """
    try:
        if doc.type != "Incoming":
            return

        phone_number = (doc.get("from") or "").strip()
        if not phone_number:
            frappe.log_error(
                title="CORTEC WhatsApp: mensaje entrante sin número",
                message=f"WhatsApp Message {doc.name} no tiene 'from'.",
            )
            return

        if _has_open_reference(doc):
            # CRM ya lo tiene vinculado a algo abierto; su propio
            # notify_agent (on_update) se encarga de notificar.
            return

        lead_name = _create_lead_from_message(doc, phone_number)
        if not lead_name:
            return

        _link_message_to_lead(doc, lead_name)

        from crm.api.whatsapp import notify_agent

        notify_agent(doc)

        frappe.logger("cortec_helpdesk").info(
            f"WhatsApp Message {doc.name} → CRM Lead {lead_name} "
            f"({phone_number})"
        )

    except Exception as e:
        frappe.log_error(
            title="CORTEC WhatsApp: error enrutando mensaje entrante",
            message=f"WhatsApp Message {doc.name}\nError: {str(e)}",
        )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _has_open_reference(doc) -> bool:
    """
    True si doc.reference_doctype/reference_name (ya fijados por
    crm.api.whatsapp.validate) apuntan a un CRM Lead o CRM Deal cuyo
    status es de tipo "Open". Un Contact suelto, o un Lead/Deal
    cerrado/ganado/perdido, cuentan como "no abierto".
    """
    doctype = doc.get("reference_doctype")
    name = doc.get("reference_name")

    if not doctype or not name or doctype not in ("CRM Lead", "CRM Deal"):
        return False

    if not frappe.db.exists(doctype, name):
        return False

    status = frappe.db.get_value(doctype, name, "status")
    if not status:
        return False

    status_doctype = "CRM Lead Status" if doctype == "CRM Lead" else "CRM Deal Status"
    try:
        status_type = frappe.get_cached_value(status_doctype, status, "type")
    except Exception:
        # Si CRM Deal Status no tuviera el campo "type" (estructura no
        # confirmada al 100%), tratamos el vínculo como NO abierto para
        # no dejar mensajes sin atender, en vez de romper el flujo.
        frappe.log_error(
            title="CORTEC WhatsApp: no se pudo leer 'type' de status",
            message=f"{status_doctype} / {status} (WhatsApp Message {doc.name})",
        )
        return False

    return status_type == "Open"


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


def _link_message_to_lead(doc, lead_name: str) -> None:
    """
    Vincula el WhatsApp Message al Lead recién creado.

    Usa frappe.db.set_value (no doc.save()) para no re-disparar
    validate/on_update de WhatsApp Message de forma anidada dentro de su
    propio after_insert. Por eso route_unclaimed_message llama a
    notify_agent manualmente después de esto.
    """
    frappe.db.set_value(
        "WhatsApp Message", doc.name,
        {
            "reference_doctype": "CRM Lead",
            "reference_name": lead_name,
        },
        update_modified=False,
    )
    # Refleja el cambio también en el objeto en memoria, ya que
    # notify_agent(doc) se llama sobre este mismo doc a continuación.
    doc.reference_doctype = "CRM Lead"
    doc.reference_name = lead_name
