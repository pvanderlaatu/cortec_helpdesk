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
cortec_helpdesk.privacy
========================
Solicitudes de supresión y revocación de los titulares (Ley 8968 y
Decreto 37554-JP). Se trabaja desde CORTEC Suppression Request.

build_plan       — busca todo lo que Frappe guarda del titular y decide
                   qué hacer con cada documento. Es el «Buscar datos».
execute_request  — aplica la resolución. Corre en la cola `long`.

Resoluciones de una supresión:

  Desasociar — anonimiza a la persona y conserva el contenido, sin sus
               identificadores (Ley 8968, art. 6.1; Decreto art. 2.r).
  Suprimir   — anonimiza a la persona y redacta el contenido, salvo el
               de documentos de una empresa cliente (tickets con
               cliente, negociaciones con organización), que es de la
               empresa y se conserva sin los identificadores del titular.
  Conservar  — no anonimiza. Solo revoca el consentimiento publicitario
               (Decreto art. 3, último párrafo; requiere habilitarlo en
               CORTEC Helpdesk Settings).

Todo se escribe con frappe.db.set_value / frappe.db.delete: un
doc.save() dejaría una Version nueva con los datos que se están
borrando y dispararía los hooks (avisos a agentes, asignación de
tickets, sincronización de consentimientos).

Qué NO puede hacer este módulo lo lista el informe como pasos manuales:
copias de seguridad, buzones IMAP, encargados externos, etc.
"""

import json
import re

import frappe
from frappe import _
from frappe.utils import add_days, add_months, cint, getdate, now_datetime, today

from cortec_helpdesk import consent_log
from cortec_helpdesk.consent_log import (
    GRANTED,
    PROMOTIONS_AGREEMENT,
    REVOKED,
    email_token,
    latest_record,
    normalize_email,
    register_consent,
    subject_token,
)

REQUEST_DOCTYPE = "CORTEC Suppression Request"
SUPPRESSED_DOCTYPE = "CORTEC Suppressed Document"

TYPE_SUPPRESSION = "Supresión"
TYPE_REVOCATION = "Revocación"

DISASSOCIATE = "Desasociar"
SUPPRESS = "Suprimir"
RETAIN = "Conservar — dato profesional"

KEEP = "conservar"
REDACT = "redactar"

REDACTED = "[Suprimido]"

# Campos de texto donde se buscan y reemplazan los identificadores.
TEXT_TYPES = {
    "Data",
    "Small Text",
    "Text",
    "Long Text",
    "Text Editor",
    "HTML Editor",
    "Markdown Editor",
    "Phone",
    "Read Only",
}
# Los que en modo «redactar» se vacían por completo: el contenido.
CONTENT_TYPES = {"Small Text", "Text", "Long Text", "Text Editor", "HTML Editor", "Markdown Editor"}
TITLE_FIELDS = {"subject", "title"}
ATTACH_TYPES = {"Attach", "Attach Image"}

# Los titulares: sus campos de identidad se sustituyen siempre.
SUBJECT_DOCTYPES = ("CRM Lead", "Contact")
PERSON_FIELDS = {
    "first_name": "name",
    "middle_name": None,
    "last_name": None,
    "full_name": "name",
    "lead_name": "name",
    "salutation": None,
    "gender": None,
    "email": "email",
    "email_id": "email",
    "mobile_no": None,
    "phone": None,
    "image": None,
}

# Documentos que cuelgan de otro: doctype → (campo del doctype
# referenciado, campo del nombre). Un campo de doctype None significa
# que el doctype referenciado es fijo (el tercer elemento).
REFERENCING = {
    "Communication": ("reference_doctype", "reference_name", None),
    "Comment": ("reference_doctype", "reference_name", None),
    "FCRM Note": ("reference_doctype", "reference_docname", None),
    "CRM Task": ("reference_doctype", "reference_docname", None),
    "CRM Call Log": ("reference_doctype", "reference_docname", None),
    "WhatsApp Message": ("reference_doctype", "reference_name", None),
    "ToDo": ("reference_type", "reference_name", None),
    "CRM Notification": ("reference_doctype", "reference_name", None),
    "HD Ticket Comment": (None, "reference_ticket", "HD Ticket"),
    "Bitrix24 Migration Log": ("target_doctype", "target_name", None),
}

# Registros derivados que se borran sin más: guardan copias de valores
# anteriores o del contenido.
DELETE_BY_REFERENCE = {
    "Version": ("ref_doctype", "docname"),
    "Activity Log": ("reference_doctype", "reference_name"),
    "View Log": ("reference_doctype", "reference_name"),
    "Notification Log": ("document_type", "document_name"),
    "Email Queue": ("reference_doctype", "reference_name"),
}

# Doctypes con números de teléfono sueltos (sin referencia a un Lead o
# Contact), que se buscan por el número.
PHONE_SEARCH = {
    "WhatsApp Message": ("from", "to"),
    "CRM Call Log": ("from", "to"),
}

# Doctypes de empresa a los que se vincula un Contact.
ORGANIZATION_DOCTYPES = ("HD Customer", "CRM Organization", "Customer")

# Nunca se tocan con el tratamiento genérico.
SKIP_DOCTYPES = {
    REQUEST_DOCTYPE,
    SUPPRESSED_DOCTYPE,
    consent_log.RECORD_DOCTYPE,
    consent_log.AGREEMENT_DOCTYPE,
}

MANUAL_STEPS = [
    "Copias de seguridad: el dato desaparece al rotar las copias existentes. Anote la fecha a partir de la cual ya no queda ninguna copia con él.",
    "Buzones IMAP de las cuentas de correo: los correos originales siguen en el servidor de correo.",
    "Mensajes ya enviados por Telegram o Raven a los agentes.",
    "Error Log y Scheduled Job Log: pueden contener fragmentos; se purgan desde Log Settings.",
    "Exportaciones o informes descargados antes de la supresión.",
]


# ---------------------------------------------------------------------------
# Titular
# ---------------------------------------------------------------------------


def split_lines(value: str | None) -> list[str]:
    return [line.strip() for line in (value or "").splitlines() if line.strip()]


def digits(value: str | None) -> str:
    return re.sub(r"\D", "", value or "")


def national_number(phone_digits: str) -> str:
    """
    Los últimos 8 dígitos: el número nacional en Costa Rica. Es lo que
    se compara, porque el mismo teléfono aparece con y sin +506.
    """
    return phone_digits[-8:] if len(phone_digits) > 8 else phone_digits


class Subject:
    """Identificadores del titular y cómo sustituirlos en un texto."""

    def __init__(self, emails, phones, names):
        self.emails = sorted({e for e in (normalize_email(x) for x in emails) if e})
        self.phones = sorted({national_number(digits(p)) for p in phones if len(digits(p)) >= 7})
        self.names: set[str] = set()
        self.add_names(names)

        seed = (self.emails or self.phones or [frappe.generate_hash(length=12)])[0]
        self.hash8 = subject_token(seed)[:8]
        self.anon_name = f"Suprimido {self.hash8}"
        self.anon_email = f"suprimido-{self.hash8}@suprimido.invalid"
        self._pattern = None

    def add_names(self, names) -> None:
        """
        Solo nombres de al menos dos palabras: reemplazar «Ana» en todo el
        historial de una empresa rompería textos que no hablan de ella.
        """
        for name in names or []:
            name = " ".join((name or "").split())
            if len(name.split()) >= 2:
                self.names.add(name)
        self._pattern = None

    @property
    def pattern(self):
        if self._pattern is None:
            parts = [re.escape(e) for e in self.emails]
            parts += [_phone_regex(p) for p in self.phones]
            parts += [
                r"(?<!\w)" + r"\s+".join(re.escape(w) for w in n.split()) + r"(?!\w)"
                for n in sorted(self.names, key=len, reverse=True)
            ]
            self._pattern = re.compile("|".join(parts), re.IGNORECASE) if parts else None
        return self._pattern

    def scrub(self, value):
        """El texto con los identificadores del titular sustituidos."""
        if not isinstance(value, str) or not value or not self.pattern:
            return value
        return self.pattern.sub(
            lambda m: self.anon_email if "@" in m.group(0) else self.anon_name, value
        )

    def is_email(self, value) -> bool:
        return normalize_email(value) in self.emails

    def tokens(self) -> list[str]:
        return [f"correo:{subject_token(e)}" for e in self.emails] + [
            f"telefono:{subject_token(p)}" for p in self.phones
        ]


def _phone_regex(national: str) -> str:
    # +506 8888-7777, 506 88887777, 8888 7777, 8888.7777…
    body = r"[\s\-.]?".join(national)
    return r"(?<!\d)(?:\+?\d{1,3}[\s\-.]?)?" + body + r"(?!\d)"


# ---------------------------------------------------------------------------
# Plan
# ---------------------------------------------------------------------------


class Plan:
    def __init__(self, request):
        self.request = request
        self.subject = Subject(
            split_lines(request.emails), split_lines(request.phones), split_lines(request.full_names)
        )
        self.leads: list[str] = []
        self.contacts: list[str] = []
        self.deals: list[str] = []
        self.tickets: list[str] = []
        self.organizations: set[tuple[str, str]] = set()
        self.last_company_activity = None
        # (doctype, nombre) → (modo, documento del que cuelga o None)
        self.documents: dict[tuple[str, str], tuple[str, tuple | None]] = {}
        self.company_documents: set[tuple[str, str]] = set()

        try:
            self.overrides = json.loads(request.content_overrides or "{}") or {}
        except ValueError:
            frappe.throw(_("«Excepciones de contenido» no es un JSON válido."))

    # -- modo ---------------------------------------------------------------

    def mode_for(self, key, parent=None) -> str:
        override = self.overrides.get(f"{key[0]}::{key[1]}")
        if override in (KEEP, REDACT):
            return override
        if self.request.resolution == DISASSOCIATE:
            return KEEP
        base = parent or key
        return KEEP if base in self.company_documents else REDACT

    def add(self, key, parent=None) -> None:
        if key not in self.documents:
            self.documents[key] = (self.mode_for(key, parent), parent)

    # -- clasificación -------------------------------------------------------

    def suggested_type(self) -> str:
        if not self.organizations or not self.last_company_activity:
            return "B2C"
        months = cint(frappe.db.get_single_value("CORTEC Helpdesk Settings", "privacy_b2b_months")) or 24
        cutoff = getdate(add_months(today(), -months))
        return "B2B" if getdate(self.last_company_activity) >= cutoff else "B2C"

    def preview(self) -> dict:
        por_doctype = {}
        documentos = []
        for (doctype, name), (mode, _parent) in sorted(self.documents.items()):
            por_doctype.setdefault(doctype, {KEEP: 0, REDACT: 0})
            por_doctype[doctype][mode] += 1
            documentos.append({"doctype": doctype, "name": name, "contenido": mode})

        return {
            "clasificacion": {
                "sugerida": self.suggested_type(),
                "empresas": sorted(f"{d}: {n}" for d, n in self.organizations),
                "ultima_actividad_empresa": str(self.last_company_activity or ""),
            },
            "titulares": {"CRM Lead": self.leads, "Contact": self.contacts},
            "por_doctype": por_doctype,
            "documentos": documentos,
            "advertencias": [
                "El reemplazo dentro del texto encuentra correos, teléfonos y nombres "
                "completos. No detecta apodos, firmas escaneadas, números de cédula ni "
                "datos escritos de otra forma: revise los documentos marcados «conservar».",
            ],
        }


def build_plan(request) -> Plan:
    plan = Plan(request)
    subject = plan.subject

    plan.leads = _find_leads(subject)
    plan.contacts = _find_contacts(subject)

    # Los nombres de los titulares encontrados también se reemplazan.
    names = []
    for lead in plan.leads:
        row = frappe.db.get_value("CRM Lead", lead, ["first_name", "last_name", "lead_name"], as_dict=True)
        names += [f"{row.first_name or ''} {row.last_name or ''}", row.lead_name]
    for contact in plan.contacts:
        row = frappe.db.get_value("Contact", contact, ["first_name", "last_name", "full_name"], as_dict=True)
        names += [f"{row.first_name or ''} {row.last_name or ''}", row.full_name, contact]
    subject.add_names(names)

    plan.deals = _find_deals(plan)
    plan.tickets = _find_tickets(plan)
    plan.organizations = _find_organizations(plan)
    plan.last_company_activity = _last_company_activity(plan)

    for deal in plan.deals:
        if _has_value("CRM Deal", deal, "organization"):
            plan.company_documents.add(("CRM Deal", deal))
    for ticket in plan.tickets:
        if _has_value("HD Ticket", ticket, "customer"):
            plan.company_documents.add(("HD Ticket", ticket))

    main = (
        [("CRM Lead", n) for n in plan.leads]
        + [("Contact", n) for n in plan.contacts]
        + [("CRM Deal", n) for n in plan.deals]
        + [("HD Ticket", n) for n in plan.tickets]
    )
    for key in main:
        plan.add(key)

    for key in list(main):
        for child in _referencing(key):
            plan.add(child, parent=key)

    for key in _communications_by_email(subject):
        plan.add(key)
    for key in _documents_by_phone(subject):
        plan.add(key)

    return plan


def _find_leads(subject: Subject) -> list[str]:
    names = set()
    if subject.emails:
        names.update(frappe.get_all("CRM Lead", filters={"email": ["in", subject.emails]}, pluck="name"))
    for field in ("mobile_no", "phone"):
        names.update(_by_phone("CRM Lead", field, subject.phones))
    return sorted(names)


def _find_contacts(subject: Subject) -> list[str]:
    names = set()
    if subject.emails:
        names.update(
            frappe.get_all("Contact Email", filters={"email_id": ["in", subject.emails]}, pluck="parent")
        )
        names.update(frappe.get_all("Contact", filters={"email_id": ["in", subject.emails]}, pluck="name"))
    names.update(_by_phone("Contact Phone", "phone", subject.phones, pluck="parent"))
    for field in ("mobile_no", "phone"):
        names.update(_by_phone("Contact", field, subject.phones))
    return sorted(names)


def _find_organizations(plan: Plan) -> set[tuple[str, str]]:
    found = set()
    if plan.contacts:
        for row in frappe.get_all(
            "Dynamic Link",
            filters={
                "parenttype": "Contact",
                "parent": ["in", plan.contacts],
                "link_doctype": ["in", ORGANIZATION_DOCTYPES],
            },
            fields=["link_doctype", "link_name"],
        ):
            found.add((row.link_doctype, row.link_name))
    for lead in plan.leads:
        organization = frappe.db.get_value("CRM Lead", lead, "organization")
        if organization and frappe.db.exists("CRM Organization", organization):
            found.add(("CRM Organization", organization))
    for deal in plan.deals:
        if _has_value("CRM Deal", deal, "organization"):
            found.add(("CRM Organization", frappe.db.get_value("CRM Deal", deal, "organization")))
    for ticket in plan.tickets:
        if _has_value("HD Ticket", ticket, "customer"):
            found.add(("HD Customer", frappe.db.get_value("HD Ticket", ticket, "customer")))
    return found


def _find_deals(plan: Plan) -> list[str]:
    if not _doctype_exists("CRM Deal"):
        return []
    names = set()
    if plan.leads and _has_field("CRM Deal", "lead"):
        names.update(frappe.get_all("CRM Deal", filters={"lead": ["in", plan.leads]}, pluck="name"))
    if plan.contacts and _doctype_exists("CRM Contacts"):
        names.update(
            frappe.get_all(
                "CRM Contacts",
                filters={"parenttype": "CRM Deal", "contact": ["in", plan.contacts]},
                pluck="parent",
            )
        )
    if plan.subject.emails and _has_field("CRM Deal", "email"):
        names.update(frappe.get_all("CRM Deal", filters={"email": ["in", plan.subject.emails]}, pluck="name"))
    return sorted(names)


def _find_tickets(plan: Plan) -> list[str]:
    if not _doctype_exists("HD Ticket"):
        return []
    names = set()
    if plan.subject.emails:
        names.update(
            frappe.get_all("HD Ticket", filters={"raised_by": ["in", plan.subject.emails]}, pluck="name")
        )
    if plan.contacts and _has_field("HD Ticket", "contact"):
        names.update(frappe.get_all("HD Ticket", filters={"contact": ["in", plan.contacts]}, pluck="name"))
    return sorted(str(n) for n in names)


def _last_company_activity(plan: Plan):
    """Última modificación de un ticket o negociación de sus empresas."""
    sources = {
        "HD Customer": ("HD Ticket", "customer"),
        "Customer": ("HD Ticket", "customer"),
        "CRM Organization": ("CRM Deal", "organization"),
    }
    dates = []
    for doctype, name in plan.organizations:
        table, field = sources.get(doctype, (None, None))
        if not table or not _has_field(table, field):
            continue
        dates.append(
            frappe.db.sql(f"select max(modified) from `tab{table}` where `{field}` = %s", name)[0][0]
        )
    dates = [d for d in dates if d]
    return max(dates) if dates else None


def _referencing(key) -> list[tuple[str, str]]:
    doctype, name = key
    found = []
    for child_doctype, (dt_field, name_field, fixed) in REFERENCING.items():
        if not _doctype_exists(child_doctype):
            continue
        if fixed:
            if fixed != doctype:
                continue
            filters = {name_field: name}
        else:
            if not (_has_field(child_doctype, dt_field) and _has_field(child_doctype, name_field)):
                continue
            filters = {dt_field: doctype, name_field: name}
        found += [(child_doctype, n) for n in frappe.get_all(child_doctype, filters=filters, pluck="name")]

    # Correos vinculados por la tabla de enlaces del timeline.
    if _doctype_exists("Communication Link"):
        found += [
            ("Communication", n)
            for n in frappe.get_all(
                "Communication Link",
                filters={"link_doctype": doctype, "link_name": name},
                pluck="parent",
            )
        ]
    return found


def _communications_by_email(subject: Subject) -> list[tuple[str, str]]:
    names = set()
    for email in subject.emails:
        names.update(frappe.get_all("Communication", filters={"sender": email}, pluck="name"))
        for field in ("recipients", "cc", "bcc"):
            names.update(
                frappe.get_all("Communication", filters={field: ["like", f"%{email}%"]}, pluck="name")
            )
    return [("Communication", n) for n in sorted(names)]


def _documents_by_phone(subject: Subject) -> list[tuple[str, str]]:
    found = []
    for doctype, fields in PHONE_SEARCH.items():
        if not _doctype_exists(doctype):
            continue
        for field in fields:
            found += [(doctype, n) for n in _by_phone(doctype, field, subject.phones)]
    return found


def _by_phone(doctype: str, field: str, phones: list[str], pluck: str = "name") -> list[str]:
    if not phones or not _has_field(doctype, field):
        return []
    clauses = " or ".join(
        f"regexp_replace(`{field}`, '[^0-9]', '') like %(p{i})s" for i in range(len(phones))
    )
    values = {f"p{i}": f"%{p}" for i, p in enumerate(phones)}
    return frappe.db.sql_list(
        f"select `{pluck}` from `tab{doctype}` where `{field}` is not null and ({clauses})", values
    )


# ---------------------------------------------------------------------------
# Ejecución
# ---------------------------------------------------------------------------


def execute_request(request_name: str) -> None:
    """Job de la cola `long`, encolado al enviar la solicitud."""
    request = frappe.get_doc(REQUEST_DOCTYPE, request_name)
    frappe.flags.in_suppression = True
    frappe.db.savepoint("privacy_request")
    try:
        report = _execute(request)
        status = "Con errores" if report["errores"] else "Completado"
    except Exception:
        frappe.db.rollback(save_point="privacy_request")
        report = {"errores": [frappe.get_traceback()]}
        status = "Con errores"
        frappe.log_error(title=f"cortec_helpdesk: falló la solicitud {request_name}")
    finally:
        frappe.flags.in_suppression = False

    values = {
        "status": status,
        "executed_on": now_datetime(),
        # El job corre como el usuario que envió la solicitud.
        "executed_by": frappe.session.user,
        "report_json": json.dumps(report, ensure_ascii=False, indent=1, default=str),
        "response_text": response_text(request, report),
    }

    if status == "Completado" and _anonymizes(request):
        # El expediente tampoco guarda los datos en claro.
        values.update(
            {
                "subject_hashes": "\n".join(report.get("identificadores", [])),
                "emails": None,
                "phones": None,
                "full_names": None,
                "preview_json": None,
            }
        )

    frappe.db.set_value(REQUEST_DOCTYPE, request_name, values, update_modified=False)
    if not frappe.flags.in_test:
        frappe.db.commit()


def _anonymizes(request) -> bool:
    return request.request_type == TYPE_SUPPRESSION and request.resolution in (DISASSOCIATE, SUPPRESS)


def _execute(request) -> dict:
    report = {
        "tipo": request.request_type,
        "resolucion": request.resolution or None,
        "conteos": {},
        "revocaciones": 0,
        "errores": [],
        "encargados": _processors(),
        "pasos_manuales": list(MANUAL_STEPS),
        "retenidos": (
            {"hasta": str(request.retained_until), "base_legal": request.legal_basis}
            if request.retained_until
            else None
        ),
    }

    plan = build_plan(request)
    report["titular_encontrado"] = bool(plan.documents)
    report["identificadores"] = plan.subject.tokens()

    # 1. Revocaciones. Van primero: si algo falla después, el titular
    # ya no recibe publicidad.
    if request.request_type == TYPE_REVOCATION:
        agreements, channel = _all_agreements(), "Solicitud del titular"
    elif request.resolution == RETAIN:
        agreements, channel = [PROMOTIONS_AGREEMENT], "Solicitud del titular"
    else:
        agreements, channel = _all_agreements(), "Supresión"

    report["revocaciones"] = _step(
        report, "revocaciones", lambda: _revoke(plan, request, agreements, channel)
    )

    if not _anonymizes(request):
        return report

    # 2. Usuarios del portal y renombrado de Contacts: cambian nombres
    # que el resto de pasos usa.
    _step(report, "usuarios", lambda: _anonymize_website_users(plan, report))
    _step(report, "renombrado", lambda: _rename_contacts(plan))

    # 3. Documentos.
    for key, (mode, _parent) in list(plan.documents.items()):
        _step(report, f"{key[0]} {key[1]}", lambda k=key, m=mode: _anonymize_document(plan, k, m, report))

    # 4. Lo que se borra.
    _step(report, "derivados", lambda: _delete_derived(plan, report))
    _step(report, "correo", lambda: _delete_mail_traces(plan, report))

    # 5. Registro de consentimientos.
    _step(report, "consentimientos", lambda: _redact_consents(plan, report))

    # 6. Marcas para las importaciones.
    _step(report, "marcas", lambda: _mark_suppressed(plan, request))

    return report


def _step(report: dict, label: str, fn):
    """Un savepoint por paso: el error de uno no deshace los demás."""
    frappe.db.savepoint("privacy_step")
    try:
        return fn()
    except Exception as e:
        frappe.db.rollback(save_point="privacy_step")
        report["errores"].append(f"{label}: {e}")
        return None


def _count(report: dict, doctype: str, action: str, n: int = 1) -> None:
    if n:
        bucket = report["conteos"].setdefault(doctype, {})
        bucket[action] = bucket.get(action, 0) + n


# -- 1. revocaciones ----------------------------------------------------------


def _all_agreements() -> list[str]:
    return frappe.get_all(consent_log.AGREEMENT_DOCTYPE, pluck="name")


def _revoke(plan: Plan, request, agreements: list[str], channel: str) -> int:
    count = 0
    subjects = [("CRM Lead", n) for n in plan.leads] + [("Contact", n) for n in plan.contacts]
    reference = subjects[0] if subjects else None

    for agreement in agreements:
        targets = [(e, None) for e in plan.subject.emails] + [(None, s) for s in subjects]
        for index, (email, ref) in enumerate(targets):
            record = latest_record(agreement, [email] if email else [], ref)
            if not record or record.action != GRANTED:
                continue
            register_consent(
                agreement,
                REVOKED,
                email,
                channel=channel,
                reference=ref or reference,
                source_reference=f"{request.name}:{agreement}:{index}",
                registered_by=request.owner,
                notes=f"Solicitud {request.name}.",
            )
            count += 1
    return count


# -- 2. usuarios y renombrado ------------------------------------------------------


def _anonymize_website_users(plan: Plan, report: dict) -> None:
    for email in plan.subject.emails:
        user = frappe.db.get_value("User", email, ["name", "user_type"], as_dict=True)
        if not user:
            continue
        if user.user_type != "Website User":
            report["pasos_manuales"].append(
                "El titular tiene un usuario interno de Frappe: desactívelo y anonimícelo a mano "
                "según el procedimiento de bajas de personal."
            )
            continue

        new_name = _unique_name("User", plan.subject.anon_email)
        frappe.rename_doc("User", user.name, new_name, force=True, ignore_permissions=True, show_alert=False)
        frappe.db.set_value(
            "User",
            new_name,
            {
                "enabled": 0,
                "email": new_name,
                "first_name": plan.subject.anon_name,
                "middle_name": None,
                "last_name": None,
                "full_name": plan.subject.anon_name,
                "username": None,
                "phone": None,
                "mobile_no": None,
                "user_image": None,
            },
            update_modified=False,
        )
        _count(report, "User", "anonimizados")


def _rename_contacts(plan: Plan) -> None:
    """
    El nombre de un Contact suele ser el de la persona. Se renombra, y
    rename_doc actualiza todos los enlaces (tickets, negociaciones, el
    ID Map de cortec_bitrix24…).
    """
    renamed = []
    for old in plan.contacts:
        new = _unique_name("Contact", plan.subject.anon_name)
        frappe.rename_doc("Contact", old, new, force=True, ignore_permissions=True, show_alert=False)
        # rename_doc deja un comentario «renamed from <nombre>».
        frappe.db.delete(
            "Comment",
            {"reference_doctype": "Contact", "reference_name": new, "content": ["like", f"%{old}%"]},
        )
        frappe.db.delete("Version", {"ref_doctype": "Contact", "docname": ["in", (old, new)]})
        renamed.append((old, new))

    for old, new in renamed:
        plan.contacts[plan.contacts.index(old)] = new
        if ("Contact", old) in plan.documents:
            plan.documents[("Contact", new)] = plan.documents.pop(("Contact", old))
        for key, (mode, parent) in list(plan.documents.items()):
            if parent == ("Contact", old):
                plan.documents[key] = (mode, ("Contact", new))


def _unique_name(doctype: str, base: str) -> str:
    name, n = base, 1
    while frappe.db.exists(doctype, name):
        n += 1
        name = f"{base}-{n}" if "@" not in base else base.replace("@", f"-{n}@")
    return name


# -- 3. documentos -------------------------------------------------------------------


def _anonymize_document(plan: Plan, key, mode: str, report: dict) -> None:
    doctype, name = key
    if doctype in SKIP_DOCTYPES or not frappe.db.exists(doctype, name):
        return

    subject = plan.subject
    meta = frappe.get_meta(doctype)
    row = frappe.db.get_value(doctype, name, "*", as_dict=True)
    is_subject = doctype in SUBJECT_DOCTYPES
    updates = _scrubbed_fields(meta, row, subject, mode)

    if is_subject or _is_subject_copy(doctype, row, plan):
        for field, kind in PERSON_FIELDS.items():
            if meta.has_field(field):
                updates[field] = (
                    subject.anon_name if kind == "name" else subject.anon_email if kind == "email" else None
                )

    for field in ("owner", "modified_by"):
        if subject.is_email(row.get(field)):
            updates[field] = subject.anon_email

    # Caché de los últimos comentarios (JSON con su texto).
    if row.get("_comments"):
        comments = None if mode == REDACT else subject.scrub(row._comments)
        if comments != row._comments:
            updates["_comments"] = comments

    if updates:
        frappe.db.set_value(doctype, name, updates, update_modified=False)

    _scrub_child_tables(meta, name, subject, mode)

    if doctype == "Contact":
        _clean_contact(plan, name)

    if mode == REDACT or is_subject:
        _delete_attachments(doctype, name, report, only_images=(mode == KEEP))

    _drop_from_global_search(doctype, name)
    _count(report, doctype, "redactados" if mode == REDACT else "anonimizados")


def _scrubbed_fields(meta, row, subject: Subject, mode: str) -> dict:
    updates = {}
    for field in meta.fields:
        value = row.get(field.fieldname)
        if not value:
            continue
        if field.fieldtype in ATTACH_TYPES and mode == REDACT:
            updates[field.fieldname] = None
            continue
        if field.fieldtype not in TEXT_TYPES or field.fieldname in PERSON_FIELDS:
            continue
        if mode == REDACT and (field.fieldtype in CONTENT_TYPES or field.fieldname in TITLE_FIELDS):
            new = REDACTED
        else:
            new = subject.scrub(value)
        if new != value:
            updates[field.fieldname] = new
    return updates


def _is_subject_copy(doctype: str, row, plan: Plan) -> bool:
    """Una negociación guarda una copia de los datos de su contacto."""
    if doctype != "CRM Deal":
        return False
    return plan.subject.is_email(row.get("email")) or row.get("lead") in plan.leads


def _scrub_child_tables(meta, name: str, subject: Subject, mode: str) -> None:
    for table in meta.get_table_fields():
        child_meta = frappe.get_meta(table.options)
        for row in frappe.get_all(
            table.options,
            filters={"parent": name, "parenttype": meta.name, "parentfield": table.fieldname},
            fields=["*"],
        ):
            updates = _scrubbed_fields(child_meta, row, subject, mode)
            if updates:
                frappe.db.set_value(table.options, row.name, updates, update_modified=False)


def _clean_contact(plan: Plan, name: str) -> None:
    frappe.db.delete("Contact Email", {"parent": name, "parenttype": "Contact"})
    frappe.db.delete("Contact Phone", {"parent": name, "parenttype": "Contact"})
    frappe.db.set_value("Contact", name, "user", None, update_modified=False)

    if plan.request.resolution == DISASSOCIATE:
        # La persona deja de figurar como contacto de la empresa; el
        # historial sigue ligado a la empresa por el ticket o la
        # negociación.
        frappe.db.delete(
            "Dynamic Link",
            {"parenttype": "Contact", "parent": name, "link_doctype": ["in", ORGANIZATION_DOCTYPES]},
        )

    # Direcciones que solo son de este contacto.
    for address in frappe.get_all(
        "Dynamic Link",
        filters={"parenttype": "Address", "link_doctype": "Contact", "link_name": name},
        pluck="parent",
    ):
        others = frappe.db.count(
            "Dynamic Link",
            {"parenttype": "Address", "parent": address, "link_name": ["!=", name]},
        )
        if not others:
            frappe.delete_doc("Address", address, ignore_permissions=True, force=True, delete_permanently=True)


def _delete_attachments(doctype: str, name: str, report: dict, only_images: bool = False) -> None:
    filters = {"attached_to_doctype": doctype, "attached_to_name": name}
    if only_images:
        filters["attached_to_field"] = "image"
    for file_name in frappe.get_all("File", filters=filters, pluck="name"):
        # delete_permanently: sin copia en Deleted Document.
        frappe.delete_doc("File", file_name, ignore_permissions=True, force=True, delete_permanently=True)
        _count(report, "File", "borrados")


def _drop_from_global_search(doctype: str, name: str) -> None:
    frappe.db.delete("__global_search", {"doctype": doctype, "name": name})


# -- 4. derivados y correo ----------------------------------------------------------


def _delete_derived(plan: Plan, report: dict) -> None:
    keys = list(plan.documents)
    for doctype, (dt_field, name_field) in DELETE_BY_REFERENCE.items():
        if not _doctype_exists(doctype):
            continue
        for ref_doctype, ref_name in keys:
            names = frappe.get_all(doctype, filters={dt_field: ref_doctype, name_field: ref_name}, pluck="name")
            if names:
                if doctype == "Email Queue":
                    frappe.db.delete("Email Queue Recipient", {"parent": ["in", names]})
                frappe.db.delete(doctype, {"name": ["in", names]})
                _count(report, doctype, "borrados", len(names))

    # Copias completas de documentos borrados antes de la supresión.
    for needle in plan.subject.emails + sorted(plan.subject.names):
        names = frappe.get_all("Deleted Document", filters={"data": ["like", f"%{needle}%"]}, pluck="name")
        if names:
            frappe.db.delete("Deleted Document", {"name": ["in", names]})
            _count(report, "Deleted Document", "borrados", len(names))


def _delete_mail_traces(plan: Plan, report: dict) -> None:
    emails = plan.subject.emails
    if not emails:
        return

    queues = frappe.get_all("Email Queue Recipient", filters={"recipient": ["in", emails]}, pluck="parent")
    if queues:
        frappe.db.delete("Email Queue Recipient", {"parent": ["in", queues]})
        frappe.db.delete("Email Queue", {"name": ["in", queues]})
        _count(report, "Email Queue", "borrados", len(set(queues)))

    # El Revocado registrado impide volver a agregarlo a la lista
    # promocional sin un consentimiento nuevo.
    for doctype in ("Email Group Member", "Email Unsubscribe"):
        names = frappe.get_all(doctype, filters={"email": ["in", emails]}, pluck="name")
        if names:
            frappe.db.delete(doctype, {"name": ["in", names]})
            _count(report, doctype, "borrados", len(names))


# -- 5. consentimientos -------------------------------------------------------------


def _redact_consents(plan: Plan, report: dict) -> None:
    """
    La única modificación permitida del registro de consentimientos: el
    correo pasa a su HMAC y se vacía la evidencia que identifica
    (IP, URL, navegador, notas). Acuerdo, versión, texto, acción, fecha
    y canal se conservan, porque la carga de la prueba del consentimiento
    es del responsable (Decreto 37554-JP, art. 6).
    """
    subjects = [("CRM Lead", n) for n in plan.leads] + [("Contact", n) for n in plan.contacts]
    names = set()
    if plan.subject.emails:
        names.update(
            frappe.get_all(consent_log.RECORD_DOCTYPE, filters={"email": ["in", plan.subject.emails]}, pluck="name")
        )
    for doctype, name in subjects:
        names.update(
            frappe.get_all(
                consent_log.RECORD_DOCTYPE,
                filters={"reference_doctype": doctype, "reference_name": name},
                pluck="name",
            )
        )

    for name in names:
        email = frappe.db.get_value(consent_log.RECORD_DOCTYPE, name, "email")
        values = {"ip_address": None, "page_url": None, "user_agent": None, "notes": None}
        if email and not email.startswith(consent_log.TOKEN_PREFIX):
            values["email"] = email_token(email)
        frappe.db.set_value(consent_log.RECORD_DOCTYPE, name, values, update_modified=False)
    _count(report, consent_log.RECORD_DOCTYPE, "redactados", len(names))

    for doctype, name in subjects:
        consent_log.sync_subject(doctype, name)


# -- 6. marcas --------------------------------------------------------------------------


def _mark_suppressed(plan: Plan, request) -> None:
    """
    Todos los documentos tocados, no solo los titulares: una corrida
    delta de cortec_bitrix24 tampoco debe reescribir una nota o un
    correo migrado con el texto original.
    """
    for doctype, name in plan.documents:
        if not frappe.db.exists(doctype, name):
            continue
        if frappe.db.exists(SUPPRESSED_DOCTYPE, {"reference_doctype": doctype, "reference_name": name}):
            continue
        frappe.get_doc(
            {
                "doctype": SUPPRESSED_DOCTYPE,
                "reference_doctype": doctype,
                "reference_name": name,
                "suppression_request": request.name,
                "suppressed_on": now_datetime(),
            }
        ).insert(ignore_permissions=True)


def is_suppressed(doctype: str, name: str) -> bool:
    """Lo consultan las importaciones antes de escribir en un documento."""
    return bool(
        frappe.db.exists(SUPPRESSED_DOCTYPE, {"reference_doctype": doctype, "reference_name": name})
    )


# ---------------------------------------------------------------------------
# Respuesta al titular
# ---------------------------------------------------------------------------


def response_text(request, report: dict) -> str:
    """
    Borrador de la respuesta (Decreto 37554-JP, arts. 20 y 22). Se
    revisa antes de enviarla; no contiene datos personales.
    """
    settings = frappe.get_cached_doc("CORTEC Helpdesk Settings")
    responsible = (settings.get("privacy_responsible") or "").strip() or "[Responsable de la base de datos]"
    received = frappe.format(request.received_on, {"fieldtype": "Date"})

    lines = [
        responsible,
        "",
        f"Referencia: {request.name}",
        f"Solicitud recibida el {received}.",
        "",
        "Estimado(a) titular:",
        "",
    ]

    found = report.get("titular_encontrado")
    conteos = report.get("conteos") or {}

    if not found and not report.get("revocaciones"):
        lines.append(
            "En atención a su solicitud le informamos que, tras revisar nuestras bases de datos, "
            "no encontramos datos personales asociados a los identificadores que nos indicó."
        )
    elif request.request_type == TYPE_REVOCATION:
        lines.append(
            "Hemos registrado la revocación de su consentimiento para el tratamiento de sus datos "
            "personales. La revocación no tiene efecto retroactivo (Ley 8968, art. 5.2)."
        )
    elif request.resolution == RETAIN:
        lines += [
            "Hemos revocado su consentimiento para recibir información promocional y lo hemos "
            "retirado de nuestras listas de envío.",
            "",
            "No procedemos con la supresión del resto de sus datos por el siguiente motivo:",
            (request.legal_basis or "").strip(),
            "",
            "Si no está de acuerdo con esta respuesta, puede acudir ante la Agencia de Protección "
            "de Datos de los Habitantes (Prodhab), conforme al Decreto 37554-JP, art. 22.",
        ]
    else:
        detalle = ", ".join(f"{doctype} ({sum(v.values())})" for doctype, v in sorted(conteos.items()))
        if request.resolution == DISASSOCIATE:
            lines.append(
                "Hemos desasociado sus datos personales de nuestros registros: su nombre, correos y "
                "teléfonos han sido sustituidos de forma irreversible, de modo que la información "
                "que conservamos de la empresa a la que representó ya no puede vincularse a usted "
                "(Ley 8968, art. 6.1)."
            )
        else:
            lines.append(
                "Hemos suprimido sus datos personales de nuestros registros de forma definitiva "
                "(Decreto 37554-JP, arts. 25 y 26)."
            )
        if detalle:
            lines += ["", f"Registros tratados: {detalle}."]
        lines += [
            "",
            "Conservamos únicamente, sin su correo en claro, la constancia de los consentimientos "
            "que otorgó y de su revocación, porque la carga de la prueba del consentimiento recae "
            "sobre el responsable de la base de datos (Decreto 37554-JP, art. 6).",
        ]
        retained = report.get("retenidos")
        if retained:
            lines += [
                "",
                f"Por disposición legal conservaremos algunos datos hasta el {retained['hasta']}: "
                f"{retained['base_legal']}",
            ]

    lines += ["", "Atentamente,", "", responsible.splitlines()[0] if responsible else ""]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Plazos
# ---------------------------------------------------------------------------


def holidays() -> set:
    raw = frappe.db.get_single_value("CORTEC Helpdesk Settings", "privacy_holidays") or ""
    found = set()
    for line in split_lines(raw):
        match = re.match(r"\d{4}-\d{2}-\d{2}", line)
        if match:
            found.add(getdate(match.group(0)))
    return found


def business_days_after(start, days: int, holiday_set: set | None = None):
    """
    La fecha en que vence un plazo de `days` días hábiles que empieza a
    correr el día siguiente a `start` (Decreto 37554-JP, art. 18).
    """
    holiday_set = holidays() if holiday_set is None else holiday_set
    current = getdate(start)
    counted = 0
    while counted < days:
        current = getdate(add_days(current, 1))
        if current.weekday() < 5 and current not in holiday_set:
            counted += 1
    return current


def _processors() -> list[dict]:
    raw = frappe.db.get_single_value("CORTEC Helpdesk Settings", "privacy_data_processors") or ""
    due = str(business_days_after(today(), 5))
    return [{"encargado": line, "informar_antes_de": due} for line in split_lines(raw)]


def daily_privacy_tasks() -> None:
    """
    Tarea diaria:
      - solicitudes cuya información adicional no llegó a tiempo pasan a
        «No presentada» (Decreto 37554-JP, art. 19);
      - aviso a los System Manager de las que vencen hoy, mañana o ya
        vencieron.
    """
    today_date = getdate(today())
    holiday_set = holidays()

    for row in frappe.get_all(
        REQUEST_DOCTYPE,
        filters={"docstatus": 0, "status": "Esperando información"},
        fields=["name", "info_requested_on"],
    ):
        if business_days_after(row.info_requested_on, 5, holiday_set) < today_date:
            frappe.db.set_value(REQUEST_DOCTYPE, row.name, "status", "No presentada", update_modified=False)

    tomorrow = business_days_after(today_date, 1, holiday_set)
    pending = frappe.get_all(
        REQUEST_DOCTYPE,
        filters={
            "docstatus": 0,
            "status": ["in", ("Borrador", "Buscado")],
            "due_date": ["<=", tomorrow],
        },
        fields=["name", "due_date"],
    )
    if not pending:
        return

    users = frappe.get_all(
        "Has Role", filters={"role": "System Manager", "parenttype": "User"}, pluck="parent"
    )
    users = [u for u in set(users) if frappe.db.get_value("User", u, "enabled") and u != "Guest"]

    for row in pending:
        state = "venció" if getdate(row.due_date) < today_date else "vence"
        subject = _("Solicitud de datos personales {0}: el plazo de respuesta {1} el {2}").format(
            row.name, state, frappe.format(row.due_date, {"fieldtype": "Date"})
        )
        for user in users:
            frappe.get_doc(
                {
                    "doctype": "Notification Log",
                    "for_user": user,
                    "type": "Alert",
                    "subject": subject,
                    "document_type": REQUEST_DOCTYPE,
                    "document_name": row.name,
                }
            ).insert(ignore_permissions=True)


# ---------------------------------------------------------------------------
# Utilidades
# ---------------------------------------------------------------------------


def _doctype_exists(doctype: str) -> bool:
    # getattr/setattr, no `frappe.local.__dict__`: en Frappe v16
    # frappe.local ya no tiene `__dict__`.
    cache = getattr(frappe.local, "_privacy_doctypes", None)
    if cache is None:
        cache = {}
        frappe.local._privacy_doctypes = cache
    if doctype not in cache:
        cache[doctype] = bool(frappe.db.exists("DocType", doctype))
    return cache[doctype]


def _has_field(doctype: str, field: str) -> bool:
    if not _doctype_exists(doctype):
        return False
    return bool(frappe.get_meta(doctype).has_field(field))


def _has_value(doctype: str, name: str, field: str) -> bool:
    return _has_field(doctype, field) and bool(frappe.db.get_value(doctype, name, field))
