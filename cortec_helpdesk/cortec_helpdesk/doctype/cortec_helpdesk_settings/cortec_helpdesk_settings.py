# Copyright (C) 2025 Corporación de Tecnología CORTEC S.R.L.
# SPDX-License-Identifier: AGPL-3.0-or-later
from __future__ import annotations

import re

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import cint

FIELD_BY_CATEGORY = {
    "helpdesk": "helpdesk_email_account",
    "crm": "crm_email_account",
}

# Grupo que despliega esta misma app en fixtures/email_group.json. Se usa
# como respaldo si el campo de Settings quedó vacío, para que la
# validación de consentimiento publicitario no quede desactivada por
# omisión en un sitio recién migrado.
DEFAULT_PROMOTIONS_EMAIL_GROUP = "Promociones CORTEC"

MIN_BROWSER_POLL_SECONDS = 5


class CORTECHelpdeskSettings(Document):
    def validate(self):
        self._validate_outgoing_enabled("helpdesk_email_account")
        self._validate_outgoing_enabled("crm_email_account")
        self._validate_telegram()
        self._validate_raven()
        self._validate_browser_alerts()

    def _validate_raven(self) -> None:
        if not self.raven_enabled:
            return
        if not is_raven_installed():
            frappe.throw(
                _("La app Raven no está instalada en este sitio: no se pueden habilitar sus avisos.")
            )
        if not self.raven_bot:
            frappe.throw(_("Elija el bot de Raven que enviará los avisos."))
        if not frappe.db.exists("Raven Bot", self.raven_bot):
            frappe.throw(_("No existe el bot de Raven {0}.").format(self.raven_bot))

    def _validate_browser_alerts(self) -> None:
        if self.browser_alerts_enabled and cint(self.browser_alert_poll_seconds) < MIN_BROWSER_POLL_SECONDS:
            frappe.throw(
                _("El intervalo de consulta de las alertas en el navegador debe ser de al menos {0} segundos.").format(
                    MIN_BROWSER_POLL_SECONDS
                )
            )

    def _validate_telegram(self) -> None:
        if self.telegram_enabled and not self.telegram_bot_token:
            frappe.throw(_("Indique el token del bot para habilitar los avisos por Telegram."))

        seen_users = set()
        for row in self.telegram_agents:
            row.chat_id = (row.chat_id or "").strip()
            if not re.fullmatch(r"-?\d+", row.chat_id):
                frappe.throw(
                    _("Fila {0}: el Chat ID de Telegram '{1}' no es válido (debe ser numérico).").format(
                        row.idx, row.chat_id
                    )
                )
            if row.user in seen_users:
                frappe.throw(
                    _("Fila {0}: el agente {1} ya está en la tabla.").format(row.idx, row.user)
                )
            seen_users.add(row.user)

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


def is_whatsapp_media_upload_enabled() -> bool:
    """
    True si los adjuntos de WhatsApp deben subirse a Meta en vez de
    enviarse como enlace (ver overrides/whatsapp_message.py).

    Por defecto activado: sin esto, ningún adjunto del CRM se envía,
    porque son archivos privados que Meta no puede descargar.
    """
    settings = frappe.get_cached_doc("CORTEC Helpdesk Settings")
    return bool(settings.get("whatsapp_upload_media"))


def get_telegram_settings():
    """
    Devuelve CORTEC Helpdesk Settings (cacheado) si los avisos por
    Telegram están habilitados, o None si no lo están.
    """
    settings = frappe.get_cached_doc("CORTEC Helpdesk Settings")
    if not settings.get("telegram_enabled"):
        return None
    return settings


def is_raven_installed() -> bool:
    """True si la app raven está instalada en este sitio."""
    return "raven" in frappe.get_installed_apps()


def get_raven_settings():
    """
    Devuelve CORTEC Helpdesk Settings (cacheado) si los avisos por Raven
    están habilitados y la app está instalada, o None si no.
    """
    if not is_raven_installed():
        return None
    settings = frappe.get_cached_doc("CORTEC Helpdesk Settings")
    if not settings.get("raven_enabled"):
        return None
    return settings


def get_browser_alert_settings():
    """
    Devuelve CORTEC Helpdesk Settings (cacheado) si las alertas en el
    navegador están habilitadas, o None si no lo están.
    """
    settings = frappe.get_cached_doc("CORTEC Helpdesk Settings")
    if not settings.get("browser_alerts_enabled"):
        return None
    return settings
