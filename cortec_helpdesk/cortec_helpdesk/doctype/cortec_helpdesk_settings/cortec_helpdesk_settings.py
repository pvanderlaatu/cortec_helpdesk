# Copyright (C) 2025 Corporación de Tecnología CORTEC S.R.L.
# SPDX-License-Identifier: AGPL-3.0-or-later
from __future__ import annotations

import frappe
from frappe import _
from frappe.model.document import Document

FIELD_BY_CATEGORY = {
    "helpdesk": "helpdesk_email_account",
    "crm": "crm_email_account",
}


class CORTECHelpdeskSettings(Document):
    def validate(self):
        self._validate_outgoing_enabled("helpdesk_email_account")
        self._validate_outgoing_enabled("crm_email_account")

    def _validate_outgoing_enabled(self, fieldname: str) -> None:
        account = self.get(fieldname)
        if not account:
            return

        enabled = frappe.db.get_value("Email Account", account, "enable_outgoing")
        if not enabled:
            frappe.throw(
                _(
                    "La cuenta de correo {0} no tiene 'Habilitar correos "
                    "salientes' activado."
                ).format(account)
            )


def get_configured_email(category: str) -> str | None:
    """
    Devuelve la dirección de correo (email_id) configurada en
    CORTEC Helpdesk Settings para la categoría dada.

    category: "helpdesk" o "crm"
    """
    fieldname = FIELD_BY_CATEGORY.get(category)
    if not fieldname:
        return None

    settings = frappe.get_cached_doc("CORTEC Helpdesk Settings")
    account_name = settings.get(fieldname)
    if not account_name:
        return None

    return frappe.db.get_value("Email Account", account_name, "email_id")


def get_configured_email_account(category: str) -> str | None:
    """
    Devuelve el nombre del documento Email Account configurado en
    CORTEC Helpdesk Settings para la categoría dada ("helpdesk" o "crm").
    """
    fieldname = FIELD_BY_CATEGORY.get(category)
    if not fieldname:
        return None

    settings = frappe.get_cached_doc("CORTEC Helpdesk Settings")
    return settings.get(fieldname) or None
