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

# Grupo que despliega esta misma app en fixtures/email_group.json. Se usa
# como respaldo si el campo de Settings quedó vacío, para que la
# validación de consentimiento publicitario no quede desactivada por
# omisión en un sitio recién migrado.
DEFAULT_PROMOTIONS_EMAIL_GROUP = "Promociones CORTEC"


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


def get_promotions_email_group() -> str:
    """
    Devuelve el Email Group configurado para correos promocionales.

    Si no está configurado, cae a DEFAULT_PROMOTIONS_EMAIL_GROUP en vez
    de devolver None: así la validación de consentimiento sigue activa
    aunque nadie haya tocado Settings todavía.
    """
    settings = frappe.get_cached_doc("CORTEC Helpdesk Settings")
    return settings.get("promotions_email_group") or DEFAULT_PROMOTIONS_EMAIL_GROUP
