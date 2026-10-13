# Copyright (C) 2026 Corporación de Tecnología CORTEC S.R.L.
# SPDX-License-Identifier: AGPL-3.0-or-later
"""
bench --site <sitio de pruebas> run-tests --app cortec_helpdesk --module cortec_helpdesk.tests.test_whatsapp_routing
"""
from __future__ import annotations

from unittest import mock

import frappe

try:
    from frappe.tests import IntegrationTestCase as TestCase
except ImportError:  # Frappe v15
    from frappe.tests.utils import FrappeTestCase as TestCase

from cortec_helpdesk.overrides import whatsapp

ACTIVO = ("CRM Lead", "CRM-LEAD-2026-00391")
CERRADO = ("CRM Deal", "CRM-DEAL-2026-00007")


def _saliente(**kwargs):
    doc = frappe._dict(
        type="Outgoing",
        reference_doctype=CERRADO[0],
        reference_name=CERRADO[1],
        **{"to": "50672098274"},
    )
    doc.update(kwargs)
    return doc


class TestSalientesHaciaLoActivo(TestCase):
    """
    El emparejamiento por teléfono de CRM lleva la respuesta del agente
    al Deal ganado de la negociación anterior, y la conversación queda
    partida: entrantes en el Lead nuevo, salientes en el Deal viejo.
    """

    def test_un_deal_cerrado_se_reapunta_al_lead_activo(self):
        doc = _saliente()
        with mock.patch.object(whatsapp, "_has_active_reference", return_value=False), \
                mock.patch.object(whatsapp, "_find_active_reference_by_phone", return_value=ACTIVO):
            whatsapp.route_outgoing_message(doc)

        self.assertEqual((doc.reference_doctype, doc.reference_name), ACTIVO)

    def test_una_referencia_en_seguimiento_no_se_toca(self):
        doc = _saliente(reference_doctype=ACTIVO[0], reference_name=ACTIVO[1])
        with mock.patch.object(whatsapp, "_has_active_reference", return_value=True), \
                mock.patch.object(whatsapp, "_find_active_reference_by_phone") as buscar:
            whatsapp.route_outgoing_message(doc)

        buscar.assert_not_called()
        self.assertEqual((doc.reference_doctype, doc.reference_name), ACTIVO)

    def test_sin_nada_activo_se_queda_donde_está(self):
        """Escribirle a un cliente desde un Deal cerrado es legítimo."""
        doc = _saliente()
        with mock.patch.object(whatsapp, "_has_active_reference", return_value=False), \
                mock.patch.object(whatsapp, "_find_active_reference_by_phone", return_value=None):
            whatsapp.route_outgoing_message(doc)

        self.assertEqual((doc.reference_doctype, doc.reference_name), CERRADO)

    def test_los_entrantes_no_pasan_por_aqui(self):
        doc = _saliente(type="Incoming")
        with mock.patch.object(whatsapp, "_find_active_reference_by_phone") as buscar:
            whatsapp.route_outgoing_message(doc)
        buscar.assert_not_called()

    def test_sin_numero_no_hace_nada(self):
        doc = _saliente(**{"to": ""})
        with mock.patch.object(whatsapp, "_has_active_reference", return_value=False), \
                mock.patch.object(whatsapp, "_find_active_reference_by_phone") as buscar:
            whatsapp.route_outgoing_message(doc)
        buscar.assert_not_called()

    def test_la_migracion_historica_no_reapunta(self):
        previo = frappe.flags.get("in_bitrix24_migration")
        frappe.flags.in_bitrix24_migration = True
        self.addCleanup(setattr, frappe.flags, "in_bitrix24_migration", previo)

        doc = _saliente()
        with mock.patch.object(whatsapp, "_find_active_reference_by_phone") as buscar:
            whatsapp.route_outgoing_message(doc)

        buscar.assert_not_called()
        self.assertEqual((doc.reference_doctype, doc.reference_name), CERRADO)
