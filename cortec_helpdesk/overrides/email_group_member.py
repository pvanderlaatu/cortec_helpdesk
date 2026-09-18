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
cortec_helpdesk.overrides.email_group_member
=============================================
Cierra el hueco que deja la validación de CRM Lead: los envíos
promocionales no se disparan desde el campo del Lead, sino desde los
registros de Email Group Member. Sin este hook, cualquiera con permisos
podría agregar un correo a la lista de promociones sin que exista
consentimiento registrado en ninguna parte.

require_registered_consent — en validate: al agregar un correo a la
                              lista promocional, exige que exista un
                              CRM Lead o un Contact con ese correo y con
                              custom_acepta_promociones marcado.

Alcance deliberadamente acotado:

  - Solo aplica al Email Group configurado en CORTEC Helpdesk Settings
    (campo "Lista de correos promocionales"). Otras listas (avisos
    internos, transaccionales) no requieren consentimiento publicitario.
  - Solo valida altas nuevas. Las actualizaciones pasan sin revisión, de
    modo que una desuscripción NUNCA pueda fallar por esta validación:
    bloquear una revocación sería peor —legal y éticamente— que el
    problema que este hook resuelve.
"""

import frappe
from frappe import _

from cortec_helpdesk.cortec_helpdesk.doctype.cortec_helpdesk_settings.cortec_helpdesk_settings import (
    get_promotions_email_group,
)
from cortec_helpdesk.overrides.consent import has_registered_consent


def require_registered_consent(doc, method: str = None) -> None:
    """
    Hook ``validate`` en Email Group Member.

    Como el hook de CRM Lead, no va envuelto en try/except: el
    frappe.throw es el comportamiento deseado.
    """
    promotions_group = get_promotions_email_group()
    if doc.get("email_group") != promotions_group:
        return

    # Una desuscripción es una actualización de este mismo documento y
    # debe poder guardarse siempre.
    if doc.get("unsubscribed"):
        return

    if not doc.is_new():
        return

    email = (doc.get("email") or "").strip()
    if not email:
        return

    if has_registered_consent(email):
        return

    frappe.throw(
        _(
            "No se puede agregar {0} a la lista '{1}': no existe ningún "
            "CRM Lead ni Contacto con ese correo que tenga registrado el "
            "consentimiento publicitario. Registre primero el "
            "consentimiento (marcando 'Acepta Correos Promocionales' e "
            "indicando su origen)."
        ).format(email, promotions_group)
    )
