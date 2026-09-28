# Copyright (C) 2026 Corporación de Tecnología CORTEC S.R.L.
# SPDX-License-Identifier: AGPL-3.0-or-later
from __future__ import annotations

import frappe
from frappe import _
from frappe.model.document import Document


class CORTECUserAgreement(Document):
    def validate(self):
        self.code = (self.code or "").strip().lower()

        # La versión es lo que permite saber qué texto aceptó cada
        # titular: cambiar el texto sin subirla haría que registros
        # anteriores apunten a un texto que nunca vieron.
        if not self.is_new() and self.has_value_changed("agreement_text"):
            self.version = (self.get_doc_before_save().version or 1) + 1

    def on_trash(self):
        if frappe.db.exists("CORTEC Consent Record", {"agreement": self.name}):
            frappe.throw(
                _(
                    "El acuerdo {0} tiene consentimientos registrados y no se "
                    "puede borrar. Desmarque «Activo» para dejar de usarlo."
                ).format(self.name)
            )
