# Copyright (C) 2026 Corporación de Tecnología CORTEC S.R.L.
# SPDX-License-Identifier: AGPL-3.0-or-later
from __future__ import annotations

"""
Pasa los consentimientos que hoy viven en campos del CRM Lead y del
Contact al registro CORTEC Consent Record (v1.0.12).

Crea los dos acuerdos por defecto con un texto provisional: el texto que
vieron esos titulares no se guardó en ningún sitio, y los registros lo
dicen así en vez de inventarlo. El texto original de cada campo se
conserva en las notas del registro, porque el campo pasa a mostrar un
resumen generado.

No toca la lista promocional: quien tenía el check ya fue suscrito por
el Worker en su momento.
"""

import re

import frappe
from frappe.utils import convert_utc_to_system_timezone, get_datetime

from cortec_helpdesk.consent_log import (
    GRANTED,
    PRIVACY_AGREEMENT,
    PROMOTIONS_AGREEMENT,
    ensure_default_agreements,
    register_consent,
)

# Fechas que el Worker y los agentes escribían en el origen:
# "2026-09-18T15:42:26.123Z", "2026-09-18 15:42", "2026-09-18".
DATE_PATTERN = re.compile(
    r"\d{4}-\d{2}-\d{2}(?:[T ]\d{2}:\d{2}(?::\d{2})?(?:\.\d+)?(?P<utc>Z)?)?"
)
URL_PATTERN = re.compile(r"https?://\S+")


def execute():
    ensure_default_agreements()

    if frappe.db.has_column("CRM Lead", "custom_acepta_promociones"):
        for row in frappe.get_all(
            "CRM Lead",
            filters={"custom_acepta_promociones": 1},
            fields=["name", "email", "creation", "custom_promociones_origen"],
        ):
            _migrate(
                "CRM Lead", row, PROMOTIONS_AGREEMENT, row.email, row.custom_promociones_origen
            )

    if frappe.db.has_column("CRM Lead", "custom_consentimiento"):
        for row in frappe.get_all(
            "CRM Lead",
            filters={"custom_consentimiento": ["is", "set"]},
            fields=["name", "email", "creation", "custom_consentimiento"],
        ):
            _migrate("CRM Lead", row, PRIVACY_AGREEMENT, row.email, row.custom_consentimiento)

    if frappe.db.has_column("Contact", "custom_acepta_promociones"):
        for row in frappe.get_all(
            "Contact",
            filters={"custom_acepta_promociones": 1},
            fields=["name", "email_id", "creation", "custom_promociones_origen"],
        ):
            _migrate(
                "Contact", row, PROMOTIONS_AGREEMENT, row.email_id, row.custom_promociones_origen
            )


def _migrate(doctype: str, row, agreement: str, email: str | None, text: str | None) -> None:
    text = (text or "").strip()

    register_consent(
        agreement,
        GRANTED,
        email,
        channel="Histórico",
        consent_datetime=_parse_date(text) or row.creation,
        page_url=_parse_url(text),
        reference=(doctype, row.name),
        source_reference=f"historico:{doctype}:{row.name}:{agreement}",
        notes=(
            f"Migrado del campo del {doctype}. Texto original: {text}"
            if text
            else f"Migrado del campo del {doctype}, sin texto de origen."
        ),
        update_mailing_list=False,
    )


def _parse_date(text: str):
    match = DATE_PATTERN.search(text or "")
    if not match:
        return None
    try:
        value = get_datetime(match.group(0).rstrip("Z").replace("T", " ")).replace(
            microsecond=0
        )
    except Exception:
        return None

    # El Worker escribía toISOString(), que está en UTC; el registro
    # guarda la hora del sistema, como el resto de Frappe.
    if match.group("utc"):
        value = convert_utc_to_system_timezone(value).replace(tzinfo=None)
    return value


def _parse_url(text: str) -> str | None:
    match = URL_PATTERN.search(text or "")
    return match.group(0).rstrip(".,;)") if match else None
