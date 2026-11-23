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
cortec_helpdesk.api.whatsapp_disponibilidad
============================================
Botón «Enviar disponibilidad (WhatsApp)» del CRM Deal: le avisa al
contacto principal que los productos del Deal ya están en inventario,
con la plantilla aprobada `disponibilidad_productos_cliente`.

  {{1}} nombre del contacto
  {{2}} artículos: "2 x HP toner negro #202A CF500A (CRC 48 508.81 c/u)"
  {{3}} total con IVA: "CRC 97 017.62"

vista_previa(deal) — arma los tres valores sin enviar nada.
enviar(deal)       — inserta el WhatsApp Message de tipo Template.

Los parámetros van en `body_param` (JSON): frappe_whatsapp usa
list(json.loads(body_param).values()) y NO lee los "Field Names" de la
plantilla, que deben quedar vacíos.

Meta rechaza parámetros con saltos de línea, tabuladores o 4+ espacios
seguidos; por eso todo pasa por _limpio().
"""

import json

import frappe
from frappe import _
from frappe.utils import flt

PLANTILLA = "disponibilidad_productos_cliente"
MAX_ARTICULOS = 900


def _limpio(v) -> str:
    """Una sola línea, sin tabs ni espacios repetidos."""
    return " ".join(str(v or "").split())


def _crc(v) -> str:
    """48508.81 -> "CRC 48 508.81" (sin comas: separador de miles = espacio)."""
    return "CRC " + f"{flt(v):,.2f}".replace(",", " ")


def _plantilla() -> str:
    """Nombre (docname) de la plantilla aprobada en WhatsApp Templates."""
    nombre = frappe.db.get_value("WhatsApp Templates", {"actual_name": PLANTILLA}, "name")
    if not nombre:
        nombre = frappe.db.get_value("WhatsApp Templates", {"template_name": PLANTILLA}, "name")
    if not nombre:
        frappe.throw(
            _("No existe la plantilla de WhatsApp «{0}». Créela en WhatsApp Templates.").format(PLANTILLA),
            title=_("Plantilla no encontrada"),
        )

    estado = frappe.db.get_value("WhatsApp Templates", nombre, "status")
    if (estado or "").upper() != "APPROVED":
        frappe.throw(
            _("La plantilla «{0}» no está aprobada por Meta (estado actual: {1}).").format(
                PLANTILLA, estado or _("sin estado")
            ),
            title=_("Plantilla no aprobada"),
        )
    return nombre


def _contacto(d) -> tuple[str, str]:
    """(first_name, móvil) del contacto principal del Deal, o del primero."""
    filas = d.get("contacts") or []
    if not filas:
        frappe.throw(
            _("El Deal {0} no tiene contactos. Agregue uno antes de enviar.").format(d.name),
            title=_("Sin contacto"),
        )
    fila = next((f for f in filas if f.get("is_primary")), filas[0])

    if not fila.get("contact"):
        frappe.throw(_("La fila de contacto del Deal {0} está vacía.").format(d.name), title=_("Sin contacto"))

    c = frappe.db.get_value("Contact", fila.contact, ["first_name", "mobile_no"], as_dict=True)
    if not c:
        frappe.throw(_("No se encontró el contacto {0}.").format(fila.contact), title=_("Sin contacto"))

    nombre = _limpio(c.first_name)
    if not nombre:
        frappe.throw(
            _("El contacto {0} no tiene nombre (First Name).").format(fila.contact),
            title=_("Contacto incompleto"),
        )

    telefono = "".join(ch for ch in str(c.mobile_no or "") if ch.isdigit())
    if not telefono:
        frappe.throw(
            _("El contacto {0} no tiene número de móvil. Agréguelo en el campo Mobile No.").format(nombre),
            title=_("Sin móvil"),
        )
    return nombre, telefono


def _armar(deal: str) -> dict:
    frappe.has_permission("CRM Deal", "write", deal, throw=True)
    d = frappe.get_doc("CRM Deal", deal)

    nombre, telefono = _contacto(d)

    productos = d.get("products") or []
    if not productos:
        frappe.throw(
            _("El Deal {0} no tiene productos. Agréguelos en la pestaña Products.").format(deal),
            title=_("Sin productos"),
        )

    partes, total = [], 0.0
    for p in productos:
        qty = flt(p.get("qty"))
        rate = flt(p.get("rate"))
        neto = flt(p.get("net_amount")) or flt(p.get("amount")) or rate * qty
        unitario = neto / qty if qty else rate
        partes.append(f"{qty:g} x {_limpio(p.get('product_name'))} ({_crc(unitario)} c/u)")
        total += neto

    articulos = _limpio("; ".join(partes))
    if len(articulos) > MAX_ARTICULOS:
        frappe.throw(
            _(
                "La lista de productos tiene {0} caracteres y el máximo es {1}. "
                "Envíe la disponibilidad en dos mensajes o acorte los nombres."
            ).format(len(articulos), MAX_ARTICULOS),
            title=_("Lista demasiado larga"),
        )

    return {"nombre": nombre, "telefono": telefono, "articulos": articulos, "total": _crc(total)}


@frappe.whitelist()
def vista_previa(deal: str) -> dict:
    """Lo que se enviaría, sin enviar nada."""
    return _armar(deal)


@frappe.whitelist(methods=["POST"])
def enviar(deal: str) -> dict:
    """Envía la plantilla al contacto principal del Deal."""
    datos = _armar(deal)
    plantilla = _plantilla()

    msg = frappe.get_doc(
        {
            "doctype": "WhatsApp Message",
            "type": "Outgoing",
            "message_type": "Template",
            "content_type": "text",
            "to": datos["telefono"],
            "template": plantilla,
            "reference_doctype": "CRM Deal",
            "reference_name": deal,
            "body_param": json.dumps(
                {"1": datos["nombre"], "2": datos["articulos"], "3": datos["total"]},
                ensure_ascii=False,
            ),
        }
    ).insert()

    return {"mensaje": msg.name, **datos}
