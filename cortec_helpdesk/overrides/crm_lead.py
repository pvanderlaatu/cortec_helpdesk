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
cortec_helpdesk.overrides.crm_lead
===================================
Trazabilidad del consentimiento publicitario en CRM Lead.

require_promotions_consent_origin — en validate: si el Lead está marcado
                                     como "Acepta Correos Promocionales",
                                     exige documentar el canal y la fecha
                                     por los que el titular consintió.

Motivo: bajo la Ley 8968 de Costa Rica, la carga de la prueba del
consentimiento recae sobre el responsable de la base de datos. Un Check
marcado, sin evidencia de su origen, no prueba que el titular haya
consentido — y el campo es editable a mano por cualquier agente.

Se validan tanto las ediciones desde la UI como las escrituras por API
(el Worker leads-intake del formulario web, imports, etc.).
"""

import frappe
from frappe import _


def require_promotions_consent_origin(doc, method: str = None) -> None:
    """
    Hook ``validate`` en CRM Lead.

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
