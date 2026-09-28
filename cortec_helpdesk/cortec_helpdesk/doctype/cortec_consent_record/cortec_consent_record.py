# Copyright (C) 2026 Corporación de Tecnología CORTEC S.R.L.
# SPDX-License-Identifier: AGPL-3.0-or-later
from __future__ import annotations

import frappe
from frappe import _
from frappe.model.document import Document

from cortec_helpdesk.consent_log import after_record_submitted


class CORTECConsentRecord(Document):
    """
    Un evento de consentimiento. Nace ya enviado (docstatus 1) y no se
    puede modificar, cancelar ni borrar: es la evidencia que exige la
    Ley 8968, y una evidencia editable no prueba nada. Un error se
    corrige registrando un evento nuevo (p. ej. un Revocado).
    """

    def validate(self):
        if self.docstatus != 1:
            frappe.throw(
                _("Los registros de consentimiento se crean ya enviados; use register_consent.")
            )

        self.email = (self.email or "").strip().lower() or None

        if not self.email and not self.reference_name:
            frappe.throw(_("El consentimiento debe tener un correo o un documento vinculado."))

    def on_submit(self):
        after_record_submitted(self)

    def on_update_after_submit(self):
        frappe.throw(_("Un registro de consentimiento no se puede modificar."))

    def on_cancel(self):
        frappe.throw(
            _("Un registro de consentimiento no se puede cancelar. Registre una revocación.")
        )

    def on_trash(self):
        frappe.throw(_("Un registro de consentimiento no se puede borrar."))
