# Copyright (C) 2026 Corporación de Tecnología CORTEC S.R.L.
# SPDX-License-Identifier: AGPL-3.0-or-later
"""
bench --site <sitio de pruebas> run-tests --app cortec_helpdesk --module cortec_helpdesk.tests.test_alerts
"""
from __future__ import annotations

from unittest import mock

import frappe

try:
    from frappe.tests import IntegrationTestCase as TestCase
except ImportError:  # Frappe v15
    from frappe.tests.utils import FrappeTestCase as TestCase

from cortec_helpdesk.overrides import alerts, whatsapp


class TestMigracionDesdeBitrix24(TestCase):
    """
    El histórico que escribe cortec_bitrix24 no debe avisar a ningún
    agente ni crear Leads por mensajes de hace años.
    """

    def setUp(self):
        previous = frappe.flags.get("in_bitrix24_migration")
        frappe.flags.in_bitrix24_migration = True
        self.addCleanup(setattr, frappe.flags, "in_bitrix24_migration", previous)

        # Que lo único que frene el aviso sea la bandera.
        patcher = mock.patch.object(alerts, "_any_channel_enabled", return_value=True)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_correo_historico_no_avisa(self):
        doc = frappe._dict(
            name="COMM-1",
            communication_medium="Email",
            sent_or_received="Received",
            reference_doctype="CRM Lead",
            reference_name="CRM-LEAD-1",
        )
        with mock.patch.object(frappe, "enqueue") as enqueue:
            alerts.on_communication(doc)
        enqueue.assert_not_called()

    def test_whatsapp_historico_no_avisa(self):
        doc = frappe._dict(name="WA-1", type="Incoming")
        with mock.patch.object(frappe, "enqueue") as enqueue:
            alerts.on_whatsapp_message(doc)
        enqueue.assert_not_called()

    def test_whatsapp_historico_no_crea_leads(self):
        doc = frappe._dict(name="WA-2", type="Incoming", **{"from": "+50688887777"})
        with mock.patch.object(whatsapp, "_create_lead_from_message") as create, mock.patch.object(
            whatsapp, "_find_active_reference_by_phone", return_value=None
        ):
            whatsapp.route_unclaimed_message(doc)
        create.assert_not_called()

    def test_fuera_de_la_migracion_si_avisa(self):
        frappe.flags.in_bitrix24_migration = False
        doc = frappe._dict(name="WA-3", type="Incoming")
        with mock.patch.object(frappe, "enqueue") as enqueue:
            alerts.on_whatsapp_message(doc)
        enqueue.assert_called_once()
