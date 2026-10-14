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
cortec_helpdesk.overrides.signature
====================================
Una firma de correo distinta en Frappe CRM y en Frappe Helpdesk.

Las dos apps leen la misma firma, User.email_signature:

  CRM       crm.api.get_user_signature
              → User.email_signature, y si está vacía, la firma de la
                cuenta saliente por defecto.
  Helpdesk  helpdesk.api.auth.get_current_user_email_info
              → solo User.email_signature.

No hay configuración que dé dos firmas distintas. Estas funciones se
registran en hooks.py con override_whitelisted_methods y **envuelven** a
las originales en vez de reescribirlas: conservan su comportamiento, sus
permisos (@agent_only en Helpdesk) y sus respaldos.

  User.custom_firma_crm        → la que ve el agente al escribir en CRM
  User.custom_firma_helpdesk   → la que ve al responder un ticket

Si el campo específico está vacío, todo sigue como antes: se usa la firma
general. Quien no llene nada no nota ningún cambio.
"""

import html

import frappe
from frappe.utils import strip_html_tags

FIELD_CRM = "custom_firma_crm"
FIELD_HELPDESK = "custom_firma_helpdesk"


# ---------------------------------------------------------------------------
# CRM
# ---------------------------------------------------------------------------

@frappe.whitelist()
def get_crm_signature():
    """
    Firma que Frappe CRM inserta al escribir un correo.

    Mismo formato que la original (``<br><p class="signature">…</p>``)
    para que el editor la trate igual. Sin firma propia del módulo,
    delega en la original, que cae en la general y luego en la de la
    cuenta de correo.
    """
    signature = _get_user_field(FIELD_CRM)
    if _has_content(signature):
        return f'<br><p class="signature">{signature}</p>'

    from crm.api import get_user_signature

    return get_user_signature()


# ---------------------------------------------------------------------------
# Helpdesk
# ---------------------------------------------------------------------------

@frappe.whitelist()
def get_helpdesk_email_info():
    """
    Datos de correo del usuario que consume el editor de Helpdesk.

    Llama a la original, que lleva @agent_only: el control de acceso
    sigue siendo el suyo. Solo sustituye ``email_signature``; el resto
    (``email``, ``outgoing_emails``, ``available_emails``) no se toca.
    """
    from helpdesk.api.auth import get_current_user_email_info

    info = get_current_user_email_info()

    signature = _get_user_field(FIELD_HELPDESK)
    if _has_content(signature) and isinstance(info, dict):
        info["email_signature"] = signature

    return info


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _get_user_field(fieldname: str) -> str | None:
    """Valor de un campo del usuario de la sesión, o None."""
    user = frappe.session.user
    if not user or user == "Guest":
        return None
    return frappe.db.get_value("User", user, fieldname)


def _has_content(value: str | None) -> bool:
    """
    True si el HTML de un Text Editor tiene algo que mostrar.

    Un editor vacío no guarda "" sino algo como
    ``<div class="ql-editor read-mode"><p><br></p></div>``, así que
    comparar con la cadena vacía tomaría una firma en blanco como real y
    taparía a la general. Se mira el texto sin etiquetas.

    Una imagen sin texto (un logo) sí cuenta como contenido.
    """
    if not value:
        return False

    if "<img" in value.lower():
        return True

    text = html.unescape(strip_html_tags(value))
    # \xa0 es el espacio duro (&nbsp;) que deja el editor.
    return bool(text.replace("\xa0", " ").strip())
