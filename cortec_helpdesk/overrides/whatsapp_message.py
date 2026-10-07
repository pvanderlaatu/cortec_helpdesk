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
cortec_helpdesk.overrides.whatsapp_message
===========================================
Hace que los adjuntos de WhatsApp SÍ se puedan enviar, sin exponerlos.

frappe_whatsapp no sube el archivo a Meta: le manda un enlace
(`get_url() + "/" + self.attach`) y le pide que lo descargue. El CRM sube
los adjuntos como privados (/private/files/...), que exigen sesión, así
que Meta recibe un 403 y el mensaje queda en "failed". Falla igual con
imágenes, documentos, videos y audios.

CORTECWhatsAppMessage interviene en dos puntos, los dos de API estable,
sin reimplementar send_outgoing:

  before_insert  — normaliza el campo `message`:
                   · nunca None, porque WhatsAppArea.vue del CRM hace
                     `whatsapp.message.startsWith('/files/')` sin
                     comprobar nulos y deja la conversación en blanco;
                   · vacío cuando es solo la ruta del adjunto (lo que
                     escribe el CRM), para que esa ruta no le llegue al
                     cliente como pie de foto.
  notify(data)   — justo antes del POST a Meta: sube el archivo al
                   endpoint /media y sustituye {"link": ...} por
                   {"id": media_id}. Así el File SIGUE SIENDO PRIVADO.

Si la subida falla, el error se propaga: el mensaje queda en "Failed" y
el motivo en Error Log. Nunca se publica un archivo automáticamente.
"""

import mimetypes
import os

import frappe
import requests
from frappe import _

from cortec_helpdesk.cortec_helpdesk.doctype.cortec_helpdesk_settings.cortec_helpdesk_settings import (
    is_whatsapp_media_upload_enabled,
)

try:
    from frappe_whatsapp.frappe_whatsapp.doctype.whatsapp_message.whatsapp_message import (
        WhatsAppMessage,
    )
except ImportError:  # Sitio sin frappe_whatsapp: la clase no se usa.
    WhatsAppMessage = object


UPLOAD_TIMEOUT = 60

# Tipos de contenido que viajan como archivo y su límite en Meta.
MEDIA_SIZE_LIMITS = {
    "image": 5 * 1024 * 1024,
    "video": 16 * 1024 * 1024,
    "audio": 16 * 1024 * 1024,
    "document": 100 * 1024 * 1024,
    "sticker": 500 * 1024,
}


class CORTECWhatsAppMessage(WhatsAppMessage):
    """WhatsApp Message que sube sus adjuntos a Meta en vez de enlazarlos."""

    def before_insert(self):
        try:
            self._normalize_message()
        except Exception as e:
            # Nunca impedir el envío por la normalización del texto.
            frappe.log_error(
                title="CORTEC WhatsApp: error normalizando el mensaje",
                message=f"{self.get('content_type')} / {self.get('attach')}\nError: {str(e)}",
            )
        super().before_insert()

    def notify(self, data):
        """
        Sustituye el enlace del adjunto por un media_id subido a Meta.

        Un payload sin adjunto (texto, plantillas, interactivos) pasa de
        largo sin tocarse.
        """
        if is_whatsapp_media_upload_enabled():
            payload = self._media_payload(data)
            if payload is not None:
                media_id = self._upload_media(payload)
                payload.pop("link", None)
                payload["id"] = media_id

        return super().notify(data)

    # -----------------------------------------------------------------------
    # before_insert
    # -----------------------------------------------------------------------

    def _normalize_message(self) -> None:
        """
        El CRM guarda la ruta del archivo dentro de `message`, y
        frappe_whatsapp la manda como caption. Se vacía para que el
        cliente no reciba la ruta, y nunca se deja en None para no
        romper la vista de WhatsApp del CRM.
        """
        message = self.get("message")

        if message is None:
            self.message = ""
            return

        attach = (self.get("attach") or "").strip()
        if attach and message.strip() == attach:
            self.message = ""

    # -----------------------------------------------------------------------
    # Subida a Meta
    # -----------------------------------------------------------------------

    def _media_payload(self, data: dict) -> dict | None:
        """
        Devuelve el sub-diccionario del payload que lleva un `link` a
        este sitio, o None si no hay adjunto que subir.
        """
        content_type = (self.get("content_type") or "").lower()
        if content_type not in MEDIA_SIZE_LIMITS:
            return None

        payload = data.get(content_type)
        if not isinstance(payload, dict):
            return None

        link = payload.get("link")
        if not link or not link.startswith(frappe.utils.get_url()):
            # Enlace externo: lo descarga Meta, no es asunto nuestro.
            return None

        return payload

    def _upload_media(self, payload: dict) -> str:
        """
        Sube el adjunto al endpoint /media y devuelve su media_id.

        Lee el archivo con get_content(), así que da igual que sea
        privado: nunca se expone por HTTP.
        """
        account = _get_account(self)
        file_doc = _get_file_doc(self.get("attach"))
        content = file_doc.get_content()
        content_type = (self.get("content_type") or "").lower()

        _validate_size(content, content_type, file_doc.file_name)

        mime_type = (
            mimetypes.guess_type(file_doc.file_name)[0] or "application/octet-stream"
        )
        token = account.get_password("token")

        response = requests.post(
            f"{account.url}/{account.version}/{account.phone_id}/media",
            headers={"Authorization": f"Bearer {token}"},
            data={"messaging_product": "whatsapp", "type": mime_type},
            files={"file": (file_doc.file_name, content, mime_type)},
            timeout=UPLOAD_TIMEOUT,
        )

        try:
            result = response.json()
        except ValueError:
            frappe.throw(
                _("Meta devolvió una respuesta ilegible al subir {0} (HTTP {1}).").format(
                    file_doc.file_name, response.status_code
                )
            )

        media_id = result.get("id")
        if not media_id:
            error = (result.get("error") or {}).get("message") or f"HTTP {response.status_code}"
            frappe.throw(
                _("Meta rechazó el archivo {0}: {1}").format(file_doc.file_name, error)
            )

        # El nombre con el que el cliente ve y guarda el documento.
        if content_type == "document":
            payload.setdefault("filename", file_doc.file_name)

        frappe.logger("cortec_helpdesk").info(
            f"WhatsApp: {file_doc.file_name} subido a Meta como {media_id}"
        )
        return media_id


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _get_account(doc):
    """
    WhatsApp Account del mensaje, con respaldo a WhatsApp Settings en
    instalaciones antiguas de frappe_whatsapp. De ahí salen url,
    version, phone_id y el token, igual que hace su propio notify().
    """
    if doc.get("whatsapp_account") and frappe.db.exists(
        "WhatsApp Account", doc.whatsapp_account
    ):
        return frappe.get_doc("WhatsApp Account", doc.whatsapp_account)

    if frappe.db.exists("DocType", "WhatsApp Settings"):
        return frappe.get_doc("WhatsApp Settings")

    frappe.throw(_("No hay una cuenta de WhatsApp configurada para enviar el adjunto."))


def _get_file_doc(attach: str | None):
    """Documento File del adjunto, sea privado o público."""
    attach = (attach or "").strip()
    if not attach:
        frappe.throw(_("El mensaje no tiene ningún archivo adjunto."))

    name = frappe.db.get_value("File", {"file_url": attach})
    if not name:
        # Un mismo archivo puede estar registrado con otro file_url
        # (p. ej. tras hacerse público): se intenta por nombre.
        name = frappe.db.get_value("File", {"file_name": os.path.basename(attach)})

    if not name:
        frappe.throw(_("No se encontró el archivo {0} en Frappe.").format(attach))

    return frappe.get_doc("File", name)


def _validate_size(content: bytes, content_type: str, file_name: str) -> None:
    limit = MEDIA_SIZE_LIMITS.get(content_type)
    if limit and len(content) > limit:
        frappe.throw(
            _("{0} pesa {1} MB y WhatsApp admite hasta {2} MB para {3}.").format(
                file_name,
                round(len(content) / 1024 / 1024, 1),
                round(limit / 1024 / 1024),
                content_type,
            )
        )


@frappe.whitelist()
def fix_null_messages() -> int:
    """
    Repara los WhatsApp Message con `message` nulo, que dejan en blanco
    la pestaña WhatsApp del CRM (WhatsAppArea.vue hace
    `message.startsWith(...)` sin comprobar nulos).

    Desde consola: cortec_helpdesk.overrides.whatsapp_message.fix_null_messages()
    """
    frappe.only_for(["System Manager"])

    rotos = frappe.get_all(
        "WhatsApp Message",
        filters={"message": ["is", "not set"]},
        fields=["name", "attach"],
    )
    for row in rotos:
        frappe.db.set_value(
            "WhatsApp Message", row.name, "message", row.attach or "",
            update_modified=False,
        )

    frappe.db.commit()
    print(f"{len(rotos)} mensajes reparados")
    return len(rotos)
