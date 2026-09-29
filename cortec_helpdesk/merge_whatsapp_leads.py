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
cortec_helpdesk.merge_whatsapp_leads
=====================================
Unifica los CRM Lead duplicados que dejó el bug corregido en v1.0.12:
antes, cada WhatsApp de un cliente cuyo Lead ya estaba en "Contacted",
"Nurture" o "Qualified" creaba un Lead nuevo.

Agrupa los Leads por los últimos 8 dígitos del teléfono, conserva el
**más antiguo**, le mueve todo el historial (conversaciones de WhatsApp,
correos, notas, tareas, llamadas, comentarios y archivos adjuntos) y
marca los demás como **Junk**. NO borra nada: los Leads sobrantes quedan
con un comentario que indica dónde quedó su historial.

Uso, desde ``bench --site <sitio> console``:

    from cortec_helpdesk.merge_whatsapp_leads import report, merge

    report()                 # solo lista los grupos duplicados
    merge()                  # simulacro: dice qué haría, sin tocar nada
    merge(dry_run=False)     # ejecuta de verdad
    merge(phone="61591066", dry_run=False)   # un solo número

Recomendación: correr ``report()``, luego ``merge()`` y revisar la
salida, y solo entonces ``merge(dry_run=False)``. Hacer un respaldo
antes (``bench --site <sitio> backup``).
"""

import frappe

from cortec_helpdesk.overrides.whatsapp import CLOSED_STATUS_TYPES, PHONE_MATCH_DIGITS


# Dónde vive el historial de un Lead: doctype → campos (doctype, nombre).
# Se comprueba que la tabla exista antes de tocarla.
REFERENCE_MAPS = (
    ("WhatsApp Message", "reference_doctype", "reference_name"),
    ("Communication", "reference_doctype", "reference_name"),
    ("Comment", "reference_doctype", "reference_name"),
    ("CRM Notification", "reference_doctype", "reference_name"),
    ("FCRM Note", "reference_doctype", "reference_docname"),
    ("CRM Task", "reference_doctype", "reference_docname"),
    ("CRM Call Log", "reference_doctype", "reference_docname"),
    ("Dynamic Link", "link_doctype", "link_name"),
    ("File", "attached_to_doctype", "attached_to_name"),
)

DEFAULT_JUNK_STATUS = "Junk"


# ---------------------------------------------------------------------------
# API
# ---------------------------------------------------------------------------

def report(phone: str | None = None) -> list[dict]:
    """Lista los grupos de Leads duplicados sin modificar nada."""
    groups = _find_groups(phone)

    if not groups:
        print("Sin Leads duplicados por teléfono.")
        return groups

    for group in groups:
        print(f"\n📞 …{group['tail']}  ({len(group['leads'])} leads)")
        for i, lead in enumerate(group["leads"]):
            marca = "conservar" if i == 0 else "→ Junk"
            convertido = " [CONVERTIDO]" if lead.converted else ""
            print(
                f"   {marca:>10}  {lead.name}  {lead.lead_name or ''} "
                f"[{lead.status}] {lead.creation:%Y-%m-%d}{convertido}"
            )
        if group["skip_reason"]:
            print(f"   ⚠️  Se omite: {group['skip_reason']}")

    print(f"\n{len(groups)} grupo(s) duplicado(s).")
    return groups


def merge(
    phone: str | None = None,
    dry_run: bool = True,
    junk_status: str = DEFAULT_JUNK_STATUS,
) -> list[dict]:
    """
    Unifica cada grupo en el Lead más antiguo.

    dry_run=True (por defecto) solo informa lo que haría.
    """
    if not dry_run:
        _validate_junk_status(junk_status)

    groups = _find_groups(phone)
    resultados = []

    for group in groups:
        if group["skip_reason"]:
            print(f"⚠️  …{group['tail']} omitido: {group['skip_reason']}")
            continue

        target = group["leads"][0]
        sources = group["leads"][1:]
        movidos_total = {}

        for source in sources:
            movidos = _move_history(source.name, target.name, dry_run)
            for doctype, n in movidos.items():
                movidos_total[doctype] = movidos_total.get(doctype, 0) + n

            if not dry_run:
                _mark_as_junk(source.name, target.name, junk_status)

        if not dry_run:
            _add_comment(
                target.name,
                "Se unificaron aquí los Leads duplicados del mismo número: "
                + ", ".join(s.name for s in sources),
            )
            frappe.db.commit()

        detalle = ", ".join(f"{k}: {v}" for k, v in sorted(movidos_total.items())) or "nada que mover"
        prefijo = "[simulacro] " if dry_run else ""
        print(
            f"{prefijo}…{group['tail']}: {len(sources)} lead(s) → {target.name}  ({detalle})"
        )

        resultados.append({
            "tail": group["tail"],
            "target": target.name,
            "merged": [s.name for s in sources],
            "moved": movidos_total,
        })

    if dry_run:
        print("\nSimulacro: no se modificó nada. Ejecute merge(dry_run=False) para aplicarlo.")

    return resultados


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _find_groups(phone: str | None = None) -> list[dict]:
    """
    Agrupa los CRM Lead por los últimos PHONE_MATCH_DIGITS dígitos del
    móvil. Devuelve solo los grupos con más de un Lead, cada uno con sus
    Leads ordenados del más antiguo al más reciente.
    """
    leads = frappe.get_all(
        "CRM Lead",
        filters={"mobile_no": ["is", "set"]},
        fields=["name", "lead_name", "mobile_no", "status", "creation", "converted"],
        order_by="creation asc",
        limit_page_length=0,
    )

    tail_filtro = _tail(phone) if phone else None

    grupos: dict[str, list] = {}
    for lead in leads:
        tail = _tail(lead.mobile_no)
        if not tail:
            continue
        if tail_filtro and tail != tail_filtro:
            continue
        grupos.setdefault(tail, []).append(lead)

    resultado = []
    for tail, items in sorted(grupos.items()):
        if len(items) < 2:
            continue
        resultado.append({
            "tail": tail,
            "leads": items,
            "skip_reason": _skip_reason(items),
        })
    return resultado


def _skip_reason(leads: list) -> str | None:
    """
    Motivo por el que un grupo NO se unifica automáticamente.

    Un Lead convertido ya tiene un CRM Deal detrás: moverle el historial
    a otro Lead dejaría la negociación incompleta, así que esos grupos se
    revisan a mano.
    """
    convertidos = [lead.name for lead in leads if lead.converted]
    if convertidos:
        return f"hay Leads convertidos a Deal ({', '.join(convertidos)}); revisar a mano"
    return None


def _tail(phone: str | None) -> str | None:
    digits = "".join(c for c in (phone or "") if c.isdigit())
    if len(digits) < PHONE_MATCH_DIGITS:
        return None
    return digits[-PHONE_MATCH_DIGITS:]


def _move_history(source: str, target: str, dry_run: bool) -> dict[str, int]:
    """Reapunta al Lead destino todo lo que colgaba del Lead origen."""
    movidos = {}

    for doctype, doctype_field, name_field in REFERENCE_MAPS:
        if not frappe.db.table_exists(doctype):
            continue

        filtros = {doctype_field: "CRM Lead", name_field: source}
        try:
            n = frappe.db.count(doctype, filtros)
        except Exception:
            # Un doctype sin esos campos en esta versión: se ignora.
            continue

        if not n:
            continue

        movidos[doctype] = n
        if dry_run:
            continue

        frappe.db.set_value(
            doctype, filtros, name_field, target, update_modified=False
        )

    return movidos


def _mark_as_junk(source: str, target: str, junk_status: str) -> None:
    """
    Marca el Lead sobrante como Junk. Se usa db.set_value para no
    disparar las automatizaciones de cambio de estado de CRM.
    """
    frappe.db.set_value("CRM Lead", source, "status", junk_status)
    _add_comment(
        source,
        f"Lead duplicado por WhatsApp. Su historial se movió al Lead {target} "
        f"y este se marcó como {junk_status}.",
    )


def _add_comment(lead: str, content: str) -> None:
    frappe.get_doc({
        "doctype": "Comment",
        "comment_type": "Comment",
        "reference_doctype": "CRM Lead",
        "reference_name": lead,
        "content": content,
    }).insert(ignore_permissions=True)


def _validate_junk_status(junk_status: str) -> None:
    if not frappe.db.exists("CRM Lead Status", junk_status):
        opciones = frappe.get_all("CRM Lead Status", pluck="name")
        frappe.throw(
            f"No existe el estado '{junk_status}'. Opciones: {', '.join(opciones)}"
        )

    tipo = frappe.db.get_value("CRM Lead Status", junk_status, "type")
    if tipo not in CLOSED_STATUS_TYPES:
        frappe.throw(
            f"El estado '{junk_status}' es de tipo '{tipo}'. Debe ser uno cerrado "
            f"({', '.join(sorted(CLOSED_STATUS_TYPES))}), o los Leads marcados "
            f"volverían a recibir los WhatsApp del cliente."
        )
