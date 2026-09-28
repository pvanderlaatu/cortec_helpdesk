# Copyright (C) 2026 Corporación de Tecnología CORTEC S.R.L.
# SPDX-License-Identifier: AGPL-3.0-or-later
from __future__ import annotations

import json

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import getdate, today

from cortec_helpdesk import privacy

# Si cambian, la vista previa ya no corresponde a lo que se ejecutaría.
PLAN_INPUTS = ("emails", "phones", "full_names", "resolution", "content_overrides", "request_type")


class CORTECSuppressionRequest(Document):
    """
    Expediente de una solicitud de supresión o revocación (Ley 8968,
    Decreto 37554-JP). Se trabaja en borrador —buscar datos, pedir
    información adicional, elegir la resolución— y al enviarlo se
    ejecuta en segundo plano (privacy.execute_request).
    """

    def validate(self):
        if self.docstatus != 0:
            return

        if not (self.emails or "").strip() and not (self.phones or "").strip():
            frappe.throw(_("Indique al menos un correo o un teléfono del titular."))

        if self.content_overrides:
            try:
                json.loads(self.content_overrides)
            except ValueError:
                frappe.throw(_("«Excepciones de contenido» no es un JSON válido."))

        holiday_set = privacy.holidays()
        start = self.info_received_on or self.received_on
        self.due_date = privacy.business_days_after(start, 5, holiday_set)
        self.cessation_due_date = (
            privacy.business_days_after(self.received_on, 3, holiday_set)
            if self.cessation_confirmation_requested
            else None
        )

        if not self.is_new() and any(self.has_value_changed(f) for f in PLAN_INPUTS):
            self.preview_json = None

        self._set_status()

    def _set_status(self):
        if self.status == "No presentada":
            return
        if self.info_requested_on and not self.info_received_on:
            self.status = "Esperando información"
        elif self.preview_json:
            self.status = "Buscado"
        else:
            self.status = "Borrador"

    def before_submit(self):
        if self.status == "No presentada":
            frappe.throw(
                _(
                    "La solicitud se tiene por no presentada: el titular no entregó la "
                    "información adicional a tiempo (Decreto 37554-JP, art. 19)."
                )
            )
        if self.status != "Buscado":
            frappe.throw(_("Use «Buscar datos» y revise el resultado antes de enviar la solicitud."))

        if self.request_type == privacy.TYPE_SUPPRESSION:
            if not self.subject_type:
                frappe.throw(_("Indique si el titular es B2B o B2C."))
            if not self.resolution:
                frappe.throw(_("Elija la resolución de la solicitud."))

        if self.resolution == privacy.RETAIN:
            if not frappe.db.get_single_value("CORTEC Helpdesk Settings", "allow_professional_retention"):
                frappe.throw(
                    _(
                        "«Conservar — dato profesional» no está habilitado en CORTEC Helpdesk "
                        "Settings. Actívelo solo cuando su asesor legal lo haya confirmado."
                    )
                )
            if not (self.legal_basis or "").strip():
                frappe.throw(_("«Conservar» exige la base legal: aparece en la negativa al titular."))

        if self.retained_until and not (self.legal_basis or "").strip():
            frappe.throw(_("Indique la base legal que obliga a retener los documentos."))

    def on_submit(self):
        self.db_set("status", "En proceso")
        frappe.enqueue(
            "cortec_helpdesk.privacy.execute_request",
            queue="long",
            timeout=60 * 60,
            request_name=self.name,
            job_name=f"suppression-{self.name}",
            enqueue_after_commit=True,
            now=bool(frappe.flags.in_test),
        )

    def on_cancel(self):
        frappe.throw(_("Un expediente de datos personales no se puede cancelar."))

    def on_trash(self):
        # También los borradores: una solicitud «no presentada» o mal
        # planteada sigue siendo prueba de que se atendió.
        frappe.throw(_("Un expediente de datos personales no se puede borrar."))


# ---------------------------------------------------------------------------
# Acciones del formulario
# ---------------------------------------------------------------------------


def _draft(name: str):
    frappe.only_for("System Manager")
    doc = frappe.get_doc(privacy.REQUEST_DOCTYPE, name)
    if doc.docstatus != 0:
        frappe.throw(_("La solicitud ya fue enviada."))
    return doc


@frappe.whitelist()
def search_data(name: str) -> dict:
    """«Buscar datos»: vista previa de lo que se haría, sin tocar nada."""
    doc = _draft(name)
    if doc.status in ("Esperando información", "No presentada"):
        frappe.throw(_("La solicitud está en estado «{0}».").format(doc.status))

    plan = privacy.build_plan(doc)
    preview = plan.preview()
    doc.preview_json = json.dumps(preview, ensure_ascii=False, indent=1, default=str)
    if not doc.subject_type:
        doc.subject_type = preview["clasificacion"]["sugerida"]
    doc.save()
    return preview["clasificacion"]


@frappe.whitelist()
def request_information(name: str) -> None:
    """Requerimiento de información adicional (Decreto 37554-JP, art. 19)."""
    doc = _draft(name)
    if doc.info_requested_on:
        frappe.throw(_("La información adicional solo se puede pedir una vez (Decreto 37554-JP, art. 19)."))
    limit = privacy.business_days_after(doc.received_on, 5)
    if getdate(today()) > limit:
        frappe.throw(
            _("Venció el plazo para pedir información adicional: debía hacerse a más tardar el {0}.").format(
                frappe.format(limit, {"fieldtype": "Date"})
            )
        )
    doc.info_requested_on = today()
    doc.save()


@frappe.whitelist()
def information_received(name: str) -> None:
    doc = _draft(name)
    if not doc.info_requested_on or doc.info_received_on:
        frappe.throw(_("No hay un requerimiento de información pendiente."))
    if doc.status == "No presentada":
        frappe.throw(_("La solicitud ya se tiene por no presentada."))
    doc.info_received_on = today()
    doc.save()


@frappe.whitelist()
def mark_response_sent(name: str) -> None:
    """Registra el envío de la respuesta y borra el medio de notificación."""
    frappe.only_for("System Manager")
    doc = frappe.get_doc(privacy.REQUEST_DOCTYPE, name)
    if doc.docstatus != 1 or doc.status not in ("Completado", "Con errores"):
        frappe.throw(_("La solicitud todavía no tiene respuesta."))
    doc.db_set({"response_sent_on": today(), "notification_medium": None})
