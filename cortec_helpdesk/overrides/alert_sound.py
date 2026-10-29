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
cortec_helpdesk.overrides.alert_sound
=====================================
Sonido de alerta propio de cada agente.

Cada usuario puede subir, en su ficha de User, un archivo de audio para
los avisos de WhatsApp y otro para los de correo:

  User.custom_sonido_whatsapp
  User.custom_sonido_correo

browser_alerts.get_new_alerts devuelve las rutas con cada consulta y
browser_alerts.js las reproduce. Con el campo vacío, o si el archivo no
se puede reproducir, suena el tono por defecto.

Un archivo se valida al guardar el usuario (tipo y tamaño) para que un
agente no suba por error algo que el navegador no pueda reproducir ni algo
tan pesado que se descargue con cada recarga de la página.
"""

import os

import frappe
from frappe import _

# Clave de alerta -> campo del User. Las claves coinciden con "kind" en
# browser_alerts (whatsapp, email).
SOUND_FIELDS = {
    "whatsapp": "custom_sonido_whatsapp",
    "email": "custom_sonido_correo",
}

ALLOWED_EXTENSIONS = (".mp3", ".wav", ".ogg")
MAX_SOUND_BYTES = 3 * 1024 * 1024  # 3 MB: un WAV sin comprimir pesa mucho


def validate_alert_sounds(doc, method: str | None = None) -> None:
    """Hook ``validate`` en User: solo audio permitido y de tamaño razonable."""
    for fieldname in SOUND_FIELDS.values():
        url = doc.get(fieldname)
        if not url or not doc.has_value_changed(fieldname):
            continue

        label = frappe.get_meta("User").get_label(fieldname)

        if not _has_allowed_extension(url):
            frappe.throw(
                _("{0}: el archivo debe ser mp3, wav u ogg.").format(label),
                title=_("Archivo de sonido no válido"),
            )

        size = frappe.db.get_value("File", {"file_url": url}, "file_size")
        if size and int(size) > MAX_SOUND_BYTES:
            frappe.throw(
                _("{0}: el archivo pesa más de {1} KB.").format(label, MAX_SOUND_BYTES // 1024),
                title=_("Archivo de sonido demasiado grande"),
            )


def get_user_sounds(user: str) -> dict[str, str | None]:
    """
    Ruta del sonido de cada tipo de alerta para el usuario, o None cuando
    no tiene uno propio (o el valor no es un audio permitido).
    """
    fieldnames = list(SOUND_FIELDS.values())
    values = frappe.db.get_value("User", user, fieldnames, as_dict=True) or {}

    return {
        kind: values.get(fieldname)
        if values.get(fieldname) and _has_allowed_extension(values.get(fieldname))
        else None
        for kind, fieldname in SOUND_FIELDS.items()
    }


def _has_allowed_extension(url: str) -> bool:
    path = url.split("?", 1)[0]
    return os.path.splitext(path)[1].lower() in ALLOWED_EXTENSIONS
