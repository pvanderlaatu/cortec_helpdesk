# Copyright (C) 2026 Corporación de Tecnología CORTEC S.R.L.
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
cortec_helpdesk.consent_log
============================
Registro de consentimientos al estilo de Bitrix24 (Ley 8968).

Cada vez que un titular otorga o revoca un consentimiento se crea un
CORTEC Consent Record inmutable, con la fecha, el canal, la IP, la URL y
una copia del texto del CORTEC User Agreement que aceptó. Es la
evidencia; todo lo demás se deriva de ella:

  custom_acepta_promociones  (CRM Lead, Contact) — último evento del
  custom_promociones_origen   acuerdo «promociones».
  custom_consentimiento      (CRM Lead)           — último evento del
                                                    acuerdo «politica-privacidad».

Esos campos son de solo lectura: se recalculan aquí cada vez que entra
un registro y en el validate del documento (overrides/consent.py).

El titular se identifica por correo y, además, por el documento al que
se vinculó el registro. Por correo, porque es lo que une a un Lead con
el Contact que nace al convertirlo y con el miembro de la lista
promocional; por documento, para los consentimientos telefónicos de un
Lead que todavía no tiene correo.

register_consent es el ÚNICO punto de entrada para crear registros:
el Worker leads-intake (api.web_lead_intake), el botón «Registrar
consentimiento», las bajas de la lista promocional, el patch de datos
históricos y la importación desde Bitrix24 pasan todos por aquí.
"""

import hashlib
import hmac

import frappe
from frappe import _
from frappe.utils import get_datetime, now_datetime
from frappe.utils.password import get_encryption_key

from cortec_helpdesk.cortec_helpdesk.doctype.cortec_helpdesk_settings.cortec_helpdesk_settings import (
    get_promotions_email_group,
)

AGREEMENT_DOCTYPE = "CORTEC User Agreement"
RECORD_DOCTYPE = "CORTEC Consent Record"

# Códigos de los acuerdos con significado para cortec_helpdesk. Puede
# haber más acuerdos; estos dos son los que alimentan campos del CRM.
PRIVACY_AGREEMENT = "politica-privacidad"
PROMOTIONS_AGREEMENT = "promociones"

GRANTED = "Otorgado"
REVOKED = "Revocado"

SUBJECT_DOCTYPES = ("CRM Lead", "Contact")

PROMOTIONS_FIELD = "custom_acepta_promociones"
PROMOTIONS_ORIGIN_FIELD = "custom_promociones_origen"
PRIVACY_FIELD = "custom_consentimiento"

# Límite de un campo Data en Frappe.
DATA_MAX_LENGTH = 140

# Canales en los que el titular consiente AHORA: pueden reactivar en la
# lista promocional a quien se había dado de baja. Un consentimiento
# histórico (Bitrix24, Histórico) nunca pasa por encima de una baja.
FRESH_CONSENT_CHANNELS = ("Formulario web", "Teléfono", "Presencial", "Correo")

# Prefijo del correo en un registro redactado por una supresión
# (privacy.py): «hmac:» seguido del HMAC-SHA256 del correo.
TOKEN_PREFIX = "hmac:"

DEFAULT_AGREEMENTS = (
    {
        "code": PRIVACY_AGREEMENT,
        "title": "Política de Privacidad",
        "kind": "Política",
        "required": 1,
    },
    {
        "code": PROMOTIONS_AGREEMENT,
        "title": "Acepto recibir información promocional",
        "kind": "Publicidad",
        "required": 0,
    },
)

PLACEHOLDER_TEXT = (
    "<p>Texto no registrado. Reemplácelo por el texto exacto que muestra el "
    "formulario web: al guardarlo el acuerdo pasa a la versión 2 y los "
    "consentimientos anteriores conservan este aviso como texto aceptado.</p>"
)


# ---------------------------------------------------------------------------
# Alta de registros
# ---------------------------------------------------------------------------


def register_consent(
    agreement: str,
    action: str,
    email: str | None = None,
    *,
    channel: str,
    consent_datetime=None,
    ip: str | None = None,
    page_url: str | None = None,
    user_agent: str | None = None,
    reference: tuple[str, str] | None = None,
    source_reference: str | None = None,
    registered_by: str | None = None,
    notes: str | None = None,
    update_mailing_list: bool = True,
) -> str:
    """
    Crea un registro de consentimiento y devuelve su nombre.

    Idempotente por (acuerdo, acción, source_reference): un reintento
    del Worker o una segunda pasada de una importación devuelven el
    registro que ya existe en vez de duplicarlo.

    `update_mailing_list=False` deja la lista promocional como está.
    Lo usan las cargas de datos históricos: suscribir de golpe a cientos
    de correos es una decisión aparte, no un efecto secundario.
    """
    if action not in (GRANTED, REVOKED):
        frappe.throw(_("Acción de consentimiento no válida: {0}").format(action))

    agreement_doc = get_agreement(agreement)
    email = normalize_email(email)
    source_reference = _clip(source_reference)

    if source_reference:
        existing = frappe.db.get_value(
            RECORD_DOCTYPE,
            {
                "agreement": agreement_doc.name,
                "action": action,
                "source_reference": source_reference,
                "docstatus": 1,
            },
            "name",
        )
        if existing:
            return existing

    reference_doctype, reference_name = reference or (None, None)

    doc = frappe.get_doc(
        {
            "doctype": RECORD_DOCTYPE,
            "docstatus": 1,
            "agreement": agreement_doc.name,
            "agreement_version": agreement_doc.version,
            "agreement_text_snapshot": agreement_doc.agreement_text,
            "action": action,
            "consent_datetime": consent_datetime or now_datetime(),
            "email": email,
            "channel": channel,
            "reference_doctype": reference_doctype if reference_name else None,
            "reference_name": reference_name,
            "registered_by": registered_by,
            "ip_address": _clip(ip),
            "page_url": (page_url or "").strip() or None,
            "user_agent": (user_agent or "").strip() or None,
            "source_reference": source_reference,
            "notes": (notes or "").strip() or None,
        }
    )
    doc.flags.update_mailing_list = update_mailing_list
    doc.insert(ignore_permissions=True)
    return doc.name


def get_agreement(code: str):
    code = (code or "").strip().lower()
    if not code or not frappe.db.exists(AGREEMENT_DOCTYPE, code):
        frappe.throw(_("No existe el acuerdo de usuario «{0}».").format(code))
    return frappe.get_cached_doc(AGREEMENT_DOCTYPE, code)


def ensure_default_agreements() -> None:
    """Crea los dos acuerdos que usa cortec_helpdesk si todavía no existen."""
    for values in DEFAULT_AGREEMENTS:
        if frappe.db.exists(AGREEMENT_DOCTYPE, values["code"]):
            continue
        frappe.get_doc(
            {
                "doctype": AGREEMENT_DOCTYPE,
                **values,
                "is_active": 1,
                "version": 1,
                "agreement_text": PLACEHOLDER_TEXT,
            }
        ).insert(ignore_permissions=True)


def after_record_submitted(record) -> None:
    """on_submit de CORTEC Consent Record."""
    for doctype, name in subjects_for(record):
        sync_subject(doctype, name)

    if (
        record.agreement == PROMOTIONS_AGREEMENT
        and record.email
        and record.flags.get("update_mailing_list", True)
    ):
        _apply_to_mailing_list(record)


# ---------------------------------------------------------------------------
# Estado vigente
# ---------------------------------------------------------------------------


def latest_record(
    agreement: str,
    emails: list[str] | None = None,
    reference: tuple[str, str] | None = None,
) -> dict | None:
    """Último evento de un acuerdo para ese titular, o None."""
    emails = [e for e in (normalize_email(x) for x in emails or []) if e]

    clauses = []
    values = {"agreement": agreement}

    if emails:
        # También los registros de un titular suprimido, que guardan el
        # HMAC del correo en vez del correo.
        clauses.append("email in %(emails)s")
        values["emails"] = tuple(emails + [email_token(e) for e in emails])

    if reference and reference[1]:
        clauses.append(
            "(reference_doctype = %(reference_doctype)s "
            "and reference_name = %(reference_name)s)"
        )
        values["reference_doctype"], values["reference_name"] = reference

    if not clauses:
        return None

    rows = frappe.db.sql(
        f"""
        select name, action, channel, consent_datetime, ip_address, page_url,
               agreement_version, registered_by
        from `tab{RECORD_DOCTYPE}`
        where docstatus = 1
          and agreement = %(agreement)s
          and ({" or ".join(clauses)})
        order by consent_datetime desc, creation desc
        limit 1
        """,
        values,
        as_dict=True,
    )
    return rows[0] if rows else None


def current_state(email: str | None, agreement: str = PROMOTIONS_AGREEMENT) -> str | None:
    """GRANTED, REVOKED o None si nunca hubo un evento para ese correo."""
    record = latest_record(agreement, [email])
    return record.action if record else None


def derived_values(
    doctype: str, name: str | None, emails: list[str]
) -> dict:
    """
    Valores que deben tener los campos de consentimiento del documento.

    Solo devuelve los acuerdos que tienen al menos un evento: un
    documento sin registros conserva lo que tuviera, en vez de ver
    borrado un texto que nadie ha migrado todavía.
    """
    reference = (doctype, name) if name else None
    values = {}

    promotions = latest_record(PROMOTIONS_AGREEMENT, emails, reference)
    if promotions:
        values[PROMOTIONS_FIELD] = 1 if promotions.action == GRANTED else 0
        values[PROMOTIONS_ORIGIN_FIELD] = summarize(promotions)

    if doctype == "CRM Lead":
        privacy = latest_record(PRIVACY_AGREEMENT, emails, reference)
        if privacy:
            values[PRIVACY_FIELD] = summarize(privacy)

    meta = frappe.get_meta(doctype)
    return {k: v for k, v in values.items() if meta.has_field(k)}


def sync_subject(doctype: str, name: str) -> None:
    """Recalcula y guarda los campos de consentimiento de un documento."""
    values = derived_values(doctype, name, subject_emails(doctype, name))
    if values:
        frappe.db.set_value(doctype, name, values, update_modified=False)


def summarize(record) -> str:
    """Texto que se muestra en los campos del Lead/Contact."""
    parts = [
        record.action,
        record.channel,
        str(get_datetime(record.consent_datetime))[:19],
    ]
    if record.get("ip_address"):
        parts.append(f"IP {record.ip_address}")
    if record.get("page_url"):
        parts.append(record.page_url)
    if record.get("registered_by"):
        parts.append(f"registrado por {record.registered_by}")
    if record.get("agreement_version"):
        parts.append(f"acuerdo v{record.agreement_version}")
    parts.append(f"registro {record.name}")
    return " · ".join(parts)


# ---------------------------------------------------------------------------
# Titulares
# ---------------------------------------------------------------------------


def subjects_for(record) -> set[tuple[str, str]]:
    """Leads y Contacts a los que afecta un registro."""
    subjects = set()

    if (
        record.reference_doctype in SUBJECT_DOCTYPES
        and record.reference_name
        and frappe.db.exists(record.reference_doctype, record.reference_name)
    ):
        subjects.add((record.reference_doctype, record.reference_name))

    if record.email:
        for name in frappe.get_all("CRM Lead", filters={"email": record.email}, pluck="name"):
            subjects.add(("CRM Lead", name))
        for name in frappe.get_all(
            "Contact Email", filters={"email_id": record.email}, pluck="parent"
        ):
            subjects.add(("Contact", name))

    return subjects


def subject_emails(doctype: str, name: str) -> list[str]:
    if doctype == "CRM Lead":
        return [frappe.db.get_value("CRM Lead", name, "email")]

    if doctype == "Contact":
        # El principal primero: es el que se usa al registrar un
        # consentimiento manual del contacto.
        emails = [frappe.db.get_value("Contact", name, "email_id")]
        emails += frappe.get_all(
            "Contact Email", filters={"parent": name}, pluck="email_id"
        )
        return emails

    return []


def document_emails(doc) -> list[str]:
    """Correos de un documento en memoria (incluye cambios sin guardar)."""
    if doc.doctype == "CRM Lead":
        return [doc.get("email")]

    if doc.doctype == "Contact":
        return [row.email_id for row in doc.get("email_ids") or []] + [doc.get("email_id")]

    return []


def normalize_email(email: str | None) -> str | None:
    email = (email or "").strip().lower()
    return email or None


def subject_token(value: str) -> str:
    """
    HMAC-SHA256 de un identificador (correo o teléfono normalizado), con
    la clave de cifrado del sitio.

    Permite responder «¿este correo dio su consentimiento, o fue
    suprimido?» sin guardar el correo. Sin la clave del sitio
    (site_config.json → encryption_key) la comprobación es imposible:
    esa clave debe estar en las copias de seguridad.
    """
    key = get_encryption_key().encode()
    return hmac.new(key, (value or "").strip().lower().encode(), hashlib.sha256).hexdigest()


def email_token(email: str) -> str:
    """Lo que guarda el campo `email` de un registro redactado."""
    return TOKEN_PREFIX + subject_token(normalize_email(email) or "")


# ---------------------------------------------------------------------------
# Lista promocional
# ---------------------------------------------------------------------------


def _apply_to_mailing_list(record) -> None:
    """
    Refleja en el Email Group promocional el estado VIGENTE del correo:
    el último evento por fecha, no el registro que acaba de entrar. Un
    consentimiento de 2024 importado hoy no suscribe a quien revocó en
    2025.

    - Último evento Revocado: se da de baja.
    - Último evento Otorgado: se suscribe, salvo baja global del sitio
      (Email Unsubscribe). A quien estaba dado de baja solo lo reactiva
      un consentimiento nuevo (FRESH_CONSENT_CHANNELS) que sea además
      ese último evento; uno histórico importado nunca.

    Dar de alta un Email Group Member no envía correo. Se escribe con
    db.set_value para no volver a disparar el hook de bajas de Email
    Group Member, que registraría otra revocación.
    """
    group = get_promotions_email_group()
    if not frappe.db.exists("Email Group", group):
        frappe.log_error(
            title="cortec_helpdesk: lista promocional inexistente",
            message=f"No existe el Email Group «{group}»; no se actualizó {record.email}.",
        )
        return

    member = frappe.db.get_value(
        "Email Group Member",
        {"email_group": group, "email": record.email},
        ["name", "unsubscribed"],
        as_dict=True,
    )

    latest = latest_record(PROMOTIONS_AGREEMENT, [record.email])

    if not latest or latest.action != GRANTED:
        if member and not member.unsubscribed:
            frappe.db.set_value("Email Group Member", member.name, "unsubscribed", 1)
        return

    if frappe.db.exists("Email Unsubscribe", {"email": record.email, "global_unsubscribe": 1}):
        return

    if not member:
        frappe.get_doc(
            {
                "doctype": "Email Group Member",
                "email_group": group,
                "email": record.email,
            }
        ).insert(ignore_permissions=True)
    elif (
        member.unsubscribed
        and latest.name == record.name
        and record.channel in FRESH_CONSENT_CHANNELS
    ):
        frappe.db.set_value("Email Group Member", member.name, "unsubscribed", 0)


def revoke_promotions_for_unsubscribe(
    email: str,
    source_reference: str,
    consent_datetime=None,
    notes: str | None = None,
    update_mailing_list: bool = False,
) -> str | None:
    """
    Registra la revocación que corresponde a una baja de correo, si el
    titular tenía el consentimiento vigente. Devuelve el registro creado.

    Por defecto no toca la lista promocional: la baja que lo origina ya
    la actualizó. La baja global sí pide actualizarla.
    """
    if current_state(email) != GRANTED:
        return None

    return register_consent(
        PROMOTIONS_AGREEMENT,
        REVOKED,
        email,
        channel="Baja por correo",
        consent_datetime=consent_datetime,
        source_reference=source_reference,
        notes=notes,
        update_mailing_list=update_mailing_list,
    )


def reconcile_unsubscribes() -> None:
    """
    Tarea diaria. Registra la revocación de cada baja de la lista
    promocional que no dejó registro.

    Existe porque la baja por el enlace de los correos de Frappe puede
    escribir `unsubscribed` directamente en la base de datos, sin pasar
    por el hook de Email Group Member. Solo cuenta la baja si es
    posterior al último consentimiento otorgado: quien volvió a aceptar
    por el formulario después de darse de baja no debe perderlo.
    """
    group = get_promotions_email_group()
    members = frappe.get_all(
        "Email Group Member",
        filters={"email_group": group, "unsubscribed": 1},
        fields=["name", "email", "modified"],
    )

    for member in members:
        last = latest_record(PROMOTIONS_AGREEMENT, [member.email])
        if not last or last.action != GRANTED:
            continue
        if get_datetime(member.modified) <= get_datetime(last.consent_datetime):
            continue

        # Un savepoint por miembro: un correo que falle no deshace las
        # revocaciones de los demás.
        frappe.db.savepoint("reconcile_unsubscribe")
        try:
            revoke_promotions_for_unsubscribe(
                member.email,
                source_reference=f"baja:{member.name}:{member.modified}",
                consent_datetime=member.modified,
                notes="Detectada por la conciliación diaria de bajas de la lista promocional.",
            )
        except Exception:
            frappe.db.rollback(save_point="reconcile_unsubscribe")
            frappe.log_error(title=f"cortec_helpdesk: no se registró la baja de {member.email}")


def _clip(value: str | None) -> str | None:
    value = (value or "").strip()
    return value[:DATA_MAX_LENGTH] or None
