# Copyright (C) 2025 Corporación de Tecnología CORTEC S.R.L.
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
cortec_helpdesk.api
====================
Endpoints para integraciones externas (hoy, el Worker leads-intake de
Cloudflare que recibe el formulario web del sitio).

subscribe_to_promotions — suscribe un correo a la lista promocional.

Existe para que el nombre del Email Group viva en UN solo lugar: el
campo "Lista de correos promocionales" de CORTEC Helpdesk Settings. Si
el Worker llamara directamente a frappe.email...add_subscribers tendría
que conocer ese nombre, y al cambiarlo en Settings el Worker seguiría
suscribiendo a la lista vieja — que además ya no sería la vigilada por
el hook de consentimiento, desactivándolo en silencio.

La validación de consentimiento NO se repite aquí: el hook validate de
Email Group Member (overrides/email_group_member.py) se dispara igual al
insertar, y es el único lugar donde debe vivir esa regla.
"""

import frappe
from frappe import _

from cortec_helpdesk.cortec_helpdesk.doctype.cortec_helpdesk_settings.cortec_helpdesk_settings import (
    get_promotions_email_group,
)


@frappe.whitelist()
def subscribe_to_promotions(email: str) -> dict:
    """
    Agrega un correo a la lista promocional configurada.

    Requiere autenticación (el Worker usa su API key/secret). Si el
    correo no tiene consentimiento registrado, el hook de Email Group
    Member aborta la inserción con un error explícito.
    """
    email = (email or "").strip()
    if not email:
        frappe.throw(_("Se requiere un correo electrónico."))

    email_group = get_promotions_email_group()

    existing = frappe.db.get_value(
        "Email Group Member",
        {"email_group": email_group, "email": email},
        ["name", "unsubscribed"],
        as_dict=True,
    )
    if existing:
        # No se reactiva automáticamente a quien se había desuscrito:
        # revertir una revocación sin decisión explícita sería peor que
        # dejarlo fuera. Se reporta para que quede visible en los logs.
        return {
            "ok": True,
            "email_group": email_group,
            "already_exists": True,
            "unsubscribed": bool(existing.unsubscribed),
        }

    frappe.get_doc(
        {
            "doctype": "Email Group Member",
            "email_group": email_group,
            "email": email,
        }
    ).insert(ignore_permissions=True)

    frappe.logger("cortec_helpdesk").info(
        f"{email} suscrito a la lista promocional '{email_group}'"
    )

    return {"ok": True, "email_group": email_group, "already_exists": False}
