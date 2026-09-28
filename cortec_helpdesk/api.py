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
cortec_helpdesk.api
====================
Endpoints para integraciones externas (hoy, el Worker leads-intake de
Cloudflare que recibe el formulario web del sitio).

web_lead_intake         — crea el CRM Lead de un envío del formulario
                          web y registra sus consentimientos, en una
                          sola transacción.

register_manual_consent — el botón «Registrar consentimiento» del Lead y
                          del Contact (teléfono, presencial, correo).

subscribe_to_promotions — suscribe un correo a la lista promocional. Lo
                          usaba el Worker antes de web_lead_intake; se
                          mantiene por compatibilidad.

Existe para que el nombre del Email Group viva en UN solo lugar: el
campo "Lista de correos promocionales" de CORTEC Helpdesk Settings. Si
el Worker llamara directamente a frappe.email...add_subscribers tendría
que conocer ese nombre, y al cambiarlo en Settings el Worker seguiría
suscribiendo a la lista vieja — que además ya no sería la vigilada por
el hook de consentimiento, desactivándolo en silencio.

La validación de consentimiento NO se repite aquí: el hook validate de
Email Group Member (overrides/email_group_member.py) se dispara igual al
insertar, y es el único lugar donde debe vivir esa regla.
"""

import frappe
from frappe import _

from cortec_helpdesk import consent_log
from cortec_helpdesk.cortec_helpdesk.doctype.cortec_helpdesk_settings.cortec_helpdesk_settings import (
    get_promotions_email_group,
)

# Campos del CRM Lead que el formulario web puede rellenar. Todo lo
# demás se descarta: en particular los campos de consentimiento, que
# solo se derivan del registro.
WEB_LEAD_FIELDS = (
    "first_name",
    "last_name",
    "email",
    "mobile_no",
    "organization",
    "website",
    "job_title",
    "custom_comentarios",
    "source",
    "status",
)

# Canales que un agente puede elegir al registrar un consentimiento a
# mano. «Formulario web», «Bitrix24», «Histórico» y «Baja por correo»
# los pone el sistema.
MANUAL_CHANNELS = ("Teléfono", "Presencial", "Correo", "Otro")


@frappe.whitelist()
def subscribe_to_promotions(email: str) -> dict:
    """
    Agrega un correo a la lista promocional configurada.

    Requiere autenticación (el Worker usa su API key/secret). Si el
    correo no tiene consentimiento registrado, el hook de Email Group
    Member aborta la inserción con un error explícito.
    """
    email = (email or "").strip()
    if not email:
        frappe.throw(_("Se requiere un correo electrónico."))

    email_group = get_promotions_email_group()

    existing = frappe.db.get_value(
        "Email Group Member",
        {"email_group": email_group, "email": email},
        ["name", "unsubscribed"],
        as_dict=True,
    )
    if existing:
        # No se reactiva automáticamente a quien se había desuscrito:
        # revertir una revocación sin decisión explícita sería peor que
        # dejarlo fuera. Se reporta para que quede visible en los logs.
        return {
            "ok": True,
            "email_group": email_group,
            "already_exists": True,
            "unsubscribed": bool(existing.unsubscribed),
        }

    frappe.get_doc(
        {
            "doctype": "Email Group Member",
            "email_group": email_group,
            "email": email,
        }
    ).insert(ignore_permissions=True)

    frappe.logger("cortec_helpdesk").info(
        f"{email} suscrito a la lista promocional '{email_group}'"
    )

    return {"ok": True, "email_group": email_group, "already_exists": False}


@frappe.whitelist(methods=["POST"])
def web_lead_intake(
    lead,
    consents,
    ip: str | None = None,
    page_url: str | None = None,
    user_agent: str | None = None,
    submission_id: str | None = None,
) -> dict:
    """
    Un envío del formulario web: crea el CRM Lead y registra un
    CORTEC Consent Record por cada acuerdo aceptado.

    `lead`     — dict con los campos de WEB_LEAD_FIELDS.
    `consents` — [{"agreement": "politica-privacidad", "accepted": true}, …]

    Todo ocurre en la transacción de la petición: si falta un acuerdo
    obligatorio o falla cualquier paso, no queda ni el Lead ni ningún
    consentimiento.

    Un reintento con el mismo `submission_id` devuelve el Lead ya
    creado en vez de duplicarlo.
    """
    lead = frappe.parse_json(lead) or {}
    consents = frappe.parse_json(consents) or []
    submission_id = (submission_id or "").strip() or None

    email = consent_log.normalize_email(lead.get("email"))
    if not email:
        frappe.throw(_("El formulario debe incluir un correo electrónico."))

    if submission_id:
        previous = frappe.db.get_value(
            consent_log.RECORD_DOCTYPE,
            {"source_reference": submission_id, "reference_doctype": "CRM Lead"},
            "reference_name",
        )
        if previous:
            return {"ok": True, "lead": previous, "duplicate": True}

    accepted = _accepted_agreements(consents)

    lead_doc = frappe.get_doc(
        {
            "doctype": "CRM Lead",
            **{k: lead[k] for k in WEB_LEAD_FIELDS if lead.get(k) not in (None, "")},
            "email": email,
        }
    ).insert()

    records = [
        consent_log.register_consent(
            code,
            consent_log.GRANTED,
            email,
            channel="Formulario web",
            ip=ip,
            page_url=page_url,
            user_agent=user_agent,
            reference=("CRM Lead", lead_doc.name),
            source_reference=submission_id,
        )
        for code in accepted
    ]

    return {"ok": True, "lead": lead_doc.name, "consents": records}


def _accepted_agreements(consents: list) -> list[str]:
    """
    Códigos aceptados en el envío, validados contra los acuerdos activos.

    Rechaza el envío si falta un acuerdo obligatorio, igual que el
    formulario de Bitrix24 con su «Política de Privacidad (obligatorio)».
    """
    active = {
        row.name: row
        for row in frappe.get_all(
            consent_log.AGREEMENT_DOCTYPE,
            filters={"is_active": 1},
            fields=["name", "title", "required"],
        )
    }

    accepted = []
    for item in consents:
        code = str((item or {}).get("agreement") or "").strip().lower()
        if not (item or {}).get("accepted"):
            continue
        if code not in active:
            frappe.throw(_("El acuerdo «{0}» no existe o no está activo.").format(code))
        if code not in accepted:
            accepted.append(code)

    missing = [row.title for code, row in active.items() if row.required and code not in accepted]
    if missing:
        frappe.throw(
            _("Falta aceptar: {0}.").format(", ".join(missing)),
            frappe.ValidationError,
        )

    return accepted


@frappe.whitelist(methods=["POST"])
def register_manual_consent(
    doctype: str,
    name: str,
    agreement: str,
    action: str,
    channel: str,
    notes: str,
) -> dict:
    """
    Registra un consentimiento dado o retirado fuera del formulario web.

    Exige permiso de escritura sobre el Lead/Contact y una nota que
    describa la evidencia (quién llamó, qué se le leyó, dónde está la
    grabación…): sin ella, el registro solo probaría que un agente pulsó
    un botón.
    """
    if doctype not in consent_log.SUBJECT_DOCTYPES:
        frappe.throw(_("Solo se registran consentimientos en CRM Lead o Contact."))

    frappe.has_permission(doctype, "write", name, throw=True)

    if channel not in MANUAL_CHANNELS:
        frappe.throw(_("Canal no válido: {0}").format(channel))

    if not (notes or "").strip():
        frappe.throw(_("Describa en las notas cómo se obtuvo el consentimiento."))

    emails = [e for e in consent_log.subject_emails(doctype, name) if e]

    record = consent_log.register_consent(
        agreement,
        action,
        emails[0] if emails else None,
        channel=channel,
        reference=(doctype, name),
        registered_by=frappe.session.user,
        notes=notes,
    )

    return {"ok": True, "record": record}
