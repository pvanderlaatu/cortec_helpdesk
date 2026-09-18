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
cortec_helpdesk.overrides.consent
==================================
Lógica compartida del consentimiento publicitario (Ley 8968 de Costa
Rica). El mismo par de campos existe en CRM Lead y en Contact:

  custom_acepta_promociones  — Check: el titular autorizó publicidad.
  custom_promociones_origen  — Small Text: canal y fecha del
                                consentimiento (la evidencia).

require_promotions_consent_origin — hook validate para AMBOS doctypes:
                                     no se puede marcar el Check sin
                                     documentar su origen.

has_registered_consent            — consulta usada por el hook de
                                     Email Group Member antes de permitir
                                     un alta en la lista promocional.

Bajo la Ley 8968 la carga de la prueba del consentimiento recae sobre el
responsable de la base de datos, y estos Checks son editables a mano por
cualquier agente: sin evidencia de origen no prueban nada.
"""

import frappe
from frappe import _


def require_promotions_consent_origin(doc, method: str = None) -> None:
    """
    Hook ``validate`` en CRM Lead y en Contact.

    No va envuelto en try/except como el resto de hooks de la app: aquí
    el frappe.throw es el comportamiento deseado, no un error que deba
    registrarse y silenciarse.
    """
    if not doc.get("custom_acepta_promociones"):
        return

    if (doc.get("custom_promociones_origen") or "").strip():
        return

    frappe.throw(
        _(
            "Para marcar 'Acepta Correos Promocionales' debe indicar el "
            "origen del consentimiento (canal y fecha) en el campo "
            "'Origen del Consentimiento Publicitario'."
        )
    )


def has_registered_consent(email: str) -> bool:
    """
    True si existe un CRM Lead o un Contact con ese correo que tenga el
    consentimiento publicitario marcado.
    """
    email = (email or "").strip()
    if not email:
        return False

    if frappe.db.exists(
        "CRM Lead", {"email": email, "custom_acepta_promociones": 1}
    ):
        return True

    return _contact_has_consent(email)


def _contact_has_consent(email: str) -> bool:
    """
    Busca por la tabla hija Contact Email (no solo por el email_id
    primario) para cubrir contactos que consintieron desde un correo
    secundario.
    """
    contacts = frappe.get_all(
        "Contact Email", filters={"email_id": email}, pluck="parent"
    )
    if not contacts:
        return False

    return bool(
        frappe.db.exists(
            "Contact",
            {"name": ["in", contacts], "custom_acepta_promociones": 1},
        )
    )
