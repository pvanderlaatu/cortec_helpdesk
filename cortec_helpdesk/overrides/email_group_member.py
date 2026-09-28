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
                              lista promocional, exige que el último
                              evento registrado para ese correo en el
                              acuerdo «promociones» sea un Otorgado.

record_unsubscribe         — en on_update: una baja de la lista
                              promocional registra la revocación del
                              consentimiento (CORTEC Consent Record).

record_global_unsubscribe  — en after_insert de Email Unsubscribe: la
                              baja global de todos los correos también
                              revoca el consentimiento publicitario.

Las bajas que escriben `unsubscribed` directamente en la base de datos,
sin pasar por estos hooks, las recoge la conciliación diaria
(consent_log.reconcile_unsubscribes).

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
from cortec_helpdesk.consent_log import revoke_promotions_for_unsubscribe
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
            "No se puede agregar {0} a la lista '{1}': ese correo no tiene "
            "un consentimiento publicitario vigente. Regístrelo primero con "
            "el botón 'Registrar consentimiento' del Lead o del Contacto."
        ).format(email, promotions_group)
    )


def record_unsubscribe(doc, method: str = None) -> None:
    """
    Hook ``on_update`` en Email Group Member.

    Va envuelto en try/except: si no se puede registrar la revocación,
    la baja se guarda igual y la conciliación diaria lo reintenta.
    Bloquear una baja por un fallo del registro sería peor que el
    registro tardío.
    """
    try:
        if doc.get("email_group") != get_promotions_email_group():
            return
        if not doc.get("unsubscribed") or not doc.has_value_changed("unsubscribed"):
            return

        revoke_promotions_for_unsubscribe(
            doc.email,
            source_reference=f"baja:{doc.name}:{doc.modified}",
            consent_datetime=doc.modified,
        )
    except Exception:
        frappe.log_error(title=f"cortec_helpdesk: no se registró la baja de {doc.get('email')}")


def record_global_unsubscribe(doc, method: str = None) -> None:
    """
    Hook ``after_insert`` en Email Unsubscribe.

    Solo las bajas globales: una baja de un documento concreto (p. ej.
    los avisos de un ticket) no dice nada sobre la publicidad.
    """
    try:
        if not doc.get("global_unsubscribe") or not doc.get("email"):
            return

        revoke_promotions_for_unsubscribe(
            doc.email,
            source_reference=f"baja-global:{doc.name}",
            consent_datetime=doc.creation,
            notes="Baja global de todos los correos del sitio.",
            update_mailing_list=True,
        )
    except Exception:
        frappe.log_error(title=f"cortec_helpdesk: no se registró la baja global de {doc.get('email')}")
