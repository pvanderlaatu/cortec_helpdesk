# Copyright (C) 2026 Corporación de Tecnología CORTEC S.R.L.
# SPDX-License-Identifier: AGPL-3.0-or-later
"""
bench --site <sitio de pruebas> run-tests --app cortec_helpdesk --module cortec_helpdesk.tests.test_raven_channel
"""
from __future__ import annotations

from unittest import mock

import frappe

try:
    from frappe.tests import IntegrationTestCase as TestCase
except ImportError:  # Frappe v15
    from frappe.tests.utils import FrappeTestCase as TestCase

from cortec_helpdesk.overrides import raven_channel


class TestRavenChannel(TestCase):
    def _doc(self, priority):
        return frappe._dict(name="1", priority=priority, flags=frappe._dict())

    def test_ticket_normal_encola_new(self):
        with mock.patch.object(raven_channel, "_settings", return_value=True), \
                mock.patch.object(frappe, "enqueue") as enqueue:
            raven_channel.on_ticket_insert(self._doc("Medium"))
        self.assertEqual(enqueue.call_args.kwargs["kind"], "new")

    def test_ticket_urgente_encola_un_solo_aviso_urgent(self):
        with mock.patch.object(raven_channel, "_settings", return_value=True), \
                mock.patch.object(frappe, "enqueue") as enqueue:
            raven_channel.on_ticket_insert(self._doc("Urgent"))
        enqueue.assert_called_once()
        self.assertEqual(enqueue.call_args.kwargs["kind"], "urgent")

    def test_sin_configurar_no_encola(self):
        with mock.patch.object(raven_channel, "_settings", return_value=None), \
                mock.patch.object(frappe, "enqueue") as enqueue:
            raven_channel.on_ticket_insert(self._doc("Urgent"))
        enqueue.assert_not_called()

    def test_migracion_bitrix24_no_avisa(self):
        previous = frappe.flags.get("in_bitrix24_migration")
        frappe.flags.in_bitrix24_migration = True
        self.addCleanup(setattr, frappe.flags, "in_bitrix24_migration", previous)
        with mock.patch.object(raven_channel, "_settings", return_value=True), \
                mock.patch.object(frappe, "enqueue") as enqueue:
            raven_channel.on_ticket_insert(self._doc("Urgent"))
        enqueue.assert_not_called()
