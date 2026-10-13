# Copyright (C) 2026 Corporación de Tecnología CORTEC S.R.L.
# SPDX-License-Identifier: AGPL-3.0-or-later
from __future__ import annotations

"""
Deja activada la subida de adjuntos de WhatsApp a Meta (v1.0.17).

El campo `whatsapp_upload_media` nació en la v1.0.16 con valor por
defecto 1, pero en un doctype Single ese valor no se escribe al migrar
si el documento ya existía: queda vacío, que se lee como apagado. El
resultado era que los adjuntos seguían fallando exactamente igual que
antes de la corrección, sin ninguna señal de por qué.

Solo toca el campo cuando está sin valor. Si alguien lo apagó a
propósito, se respeta.
"""

import frappe

SETTINGS = "CORTEC Helpdesk Settings"
FIELD = "whatsapp_upload_media"


def execute():
    if not frappe.db.exists("DocType", SETTINGS):
        return

    actual = frappe.db.get_single_value(SETTINGS, FIELD)
    if actual not in (None, ""):
        return

    frappe.db.set_single_value(SETTINGS, FIELD, 1)
    frappe.clear_cache(doctype=SETTINGS)

    frappe.logger("cortec_helpdesk").info(
        "Subida de adjuntos de WhatsApp a Meta activada por el patch"
    )
