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
Consentimiento publicitario y de políticas (Ley 8968 de Costa Rica).

La evidencia vive en CORTEC Consent Record (ver consent_log.py). Los
campos del CRM Lead y del Contact son un reflejo de ese registro:

  custom_acepta_promociones  — Check: último evento de «promociones».
  custom_promociones_origen  — Small Text: resumen de ese evento.
  custom_consentimiento      — Small Text (solo CRM Lead): resumen del
                                último evento de «politica-privacidad».

sync_consent_fields     — hook validate para AMBOS doctypes: recalcula
                          los campos desde el registro e impide
                          marcarlos o desmarcarlos a mano.

has_registered_consent  — consulta usada por el hook de Email Group
                          Member antes de permitir un alta en la lista
                          promocional.

Bajo la Ley 8968 la carga de la prueba del consentimiento recae sobre el
responsable de la base de datos. Un Check que cualquier agente puede
marcar no prueba nada; un registro inmutable con fecha, canal y texto
aceptado, sí.
"""

import frappe
from frappe import _
from frappe.utils import cint

from cortec_helpdesk.consent_log import (
    GRANTED,
    PROMOTIONS_AGREEMENT,
    PROMOTIONS_FIELD,
    current_state,
    derived_values,
    document_emails,
)


def sync_consent_fields(doc, method: str = None) -> None:
    """
    Hook ``validate`` en CRM Lead y en Contact.

    No va envuelto en try/except como el resto de hooks de la app: aquí
    el frappe.throw es el comportamiento deseado, no un error que deba
    registrarse y silenciarse.
    """
    if not doc.meta.has_field(PROMOTIONS_FIELD):
        return

    name = None if doc.is_new() else doc.name
    derived = derived_values(doc.doctype, name, document_emails(doc))

    requested = cint(doc.get(PROMOTIONS_FIELD))
    registered = cint(derived.get(PROMOTIONS_FIELD))
    touched = doc.is_new() or doc.has_value_changed(PROMOTIONS_FIELD)

    if touched and requested and not registered:
        frappe.throw(
            _(
                "'Acepta Correos Promocionales' no se marca a mano: use el botón "
                "'Registrar consentimiento' para dejar constancia del canal, la "
                "fecha y el texto que aceptó el titular."
            )
        )

    if touched and not doc.is_new() and not requested and registered:
        frappe.throw(
            _(
                "Para retirar el consentimiento publicitario registre una "
                "revocación con el botón 'Registrar consentimiento': el registro "
                "debe mostrar cuándo y por qué canal se retiró."
            )
        )

    doc.update(derived)


def has_registered_consent(email: str) -> bool:
    """
    True si el último evento registrado para ese correo en el acuerdo
    «promociones» es un consentimiento otorgado.
    """
    email = (email or "").strip()
    if not email:
        return False

    return current_state(email, PROMOTIONS_AGREEMENT) == GRANTED
