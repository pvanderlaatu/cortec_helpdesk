# Copyright (C) 2026 Corporación de Tecnología CORTEC S.R.L.
# SPDX-License-Identifier: AGPL-3.0-or-later
"""
bench --site <sitio de pruebas> run-tests --app cortec_helpdesk --module cortec_helpdesk.tests.test_privacy
"""
from __future__ import annotations

import json
from datetime import date

import frappe

try:
    from frappe.tests import IntegrationTestCase as TestCase
except ImportError:  # Frappe v15
    from frappe.tests.utils import FrappeTestCase as TestCase

from cortec_helpdesk import consent_log, privacy
from cortec_helpdesk.consent_log import GRANTED, PROMOTIONS_AGREEMENT, REVOKED, register_consent
from cortec_helpdesk.cortec_helpdesk.doctype.cortec_suppression_request import (
    cortec_suppression_request as request_api,
)


class TestSubject(TestCase):
    def test_reemplaza_correo_telefono_y_nombre(self):
        subject = privacy.Subject(["Ana.Perez@Example.com"], ["+506 8888-7777"], ["Ana Pérez Mora"])
        text = (
            "Escribió ana.perez@example.com desde el 8888 7777 (tel. +50688887777). "
            "Firma: Ana  Pérez Mora. Caso de la empresa: impresora averiada."
        )
        scrubbed = subject.scrub(text)
        self.assertNotIn("example.com", scrubbed.lower())
        self.assertNotIn("8888", scrubbed)
        self.assertNotIn("Pérez", scrubbed)
        self.assertIn("impresora averiada", scrubbed)
        self.assertIn(subject.anon_email, scrubbed)

    def test_no_reemplaza_nombres_sueltos(self):
        # «Ana» sola rompería textos que no hablan de ella.
        subject = privacy.Subject(["a@example.com"], [], ["Ana"])
        self.assertEqual(subject.scrub("Ana revisó el equipo"), "Ana revisó el equipo")

    def test_no_confunde_numeros_mas_largos(self):
        subject = privacy.Subject([], ["88887777"], [])
        self.assertEqual(subject.scrub("Serie 1888877771"), "Serie 1888877771")


class TestPlazos(TestCase):
    def test_cinco_dias_habiles_desde_el_dia_siguiente(self):
        # Viernes 2 de octubre de 2026 → cuenta desde el lunes 5.
        self.assertEqual(privacy.business_days_after(date(2026, 10, 2), 5, set()), date(2026, 10, 9))

    def test_descuenta_feriados(self):
        feriados = {date(2026, 10, 5)}
        self.assertEqual(privacy.business_days_after(date(2026, 10, 2), 5, feriados), date(2026, 10, 12))


class TestSuppression(TestCase):
    def setUp(self):
        consent_log.ensure_default_agreements()
        frappe.db.set_single_value("CORTEC Helpdesk Settings", "allow_professional_retention", 0)
        self.email = f"titular-{frappe.generate_hash(length=8)}@example.com"
        self.name = f"Titular {frappe.generate_hash(length=6)} Prueba"
        first, middle, last = self.name.split()
        self.lead = frappe.get_doc(
            {
                "doctype": "CRM Lead",
                "first_name": first,
                "last_name": f"{middle} {last}",
                "email": self.email,
                "mobile_no": "+50688887777",
            }
        ).insert(ignore_permissions=True)
        self.contact = frappe.get_doc(
            {
                "doctype": "Contact",
                "first_name": first,
                "last_name": f"{middle} {last}",
                "email_ids": [{"email_id": self.email, "is_primary": 1}],
                "phone_nos": [{"phone": "8888-7777", "is_primary_mobile_no": 1}],
            }
        ).insert(ignore_permissions=True)
        self.communication = frappe.get_doc(
            {
                "doctype": "Communication",
                "communication_type": "Communication",
                "communication_medium": "Email",
                "sent_or_received": "Received",
                "sender": self.email,
                "subject": f"Consulta de {self.name}",
                "content": f"Hola, soy {self.name}, mi teléfono es 8888 7777.",
                "reference_doctype": "CRM Lead",
                "reference_name": self.lead.name,
            }
        ).insert(ignore_permissions=True)
        register_consent(
            PROMOTIONS_AGREEMENT,
            GRANTED,
            self.email,
            channel="Formulario web",
            ip="190.113.106.79",
            page_url="https://tecnocr.net/contacto",
            reference=("CRM Lead", self.lead.name),
        )

    def tearDown(self):
        frappe.db.rollback()

    def _request(self, **values):
        doc = frappe.get_doc(
            {
                "doctype": privacy.REQUEST_DOCTYPE,
                "request_type": "Supresión",
                "received_on": frappe.utils.today(),
                "notification_medium": self.email,
                "identity_verification": "Cédula revisada en persona.",
                "emails": self.email,
                "phones": "8888-7777",
                **values,
            }
        ).insert(ignore_permissions=True)
        request_api.search_data(doc.name)
        return frappe.get_doc(privacy.REQUEST_DOCTYPE, doc.name)

    def _leaks(self) -> list[str]:
        """Tablas tocadas que todavía contienen el correo, el nombre o el teléfono."""
        needles = [self.email, self.name, "88887777", "8888-7777", "8888 7777"]
        tables = ["CRM Lead", "Contact", "Contact Email", "Contact Phone", "Communication", "Version"]
        leaks = []
        for table in tables:
            columns = [
                c[0]
                for c in frappe.db.sql(
                    "select column_name from information_schema.columns "
                    "where table_schema = database() and table_name = %s "
                    "and data_type in ('varchar', 'text', 'longtext', 'mediumtext')",
                    f"tab{table}",
                )
            ]
            for needle in needles:
                where = " or ".join(f"`{c}` like %(n)s" for c in columns)
                if where and frappe.db.sql(f"select 1 from `tab{table}` where {where} limit 1", {"n": f"%{needle}%"}):
                    leaks.append(f"{table}: {needle}")
        return leaks

    def test_buscar_datos_encuentra_al_titular(self):
        request = self._request()
        preview = json.loads(request.preview_json)
        self.assertEqual(request.status, "Buscado")
        self.assertIn(self.lead.name, preview["titulares"]["CRM Lead"])
        self.assertIn(self.contact.name, preview["titulares"]["Contact"])
        self.assertEqual(preview["clasificacion"]["sugerida"], "B2C")

    def test_suprimir_no_deja_rastro(self):
        request = self._request(subject_type="B2C", resolution="Suprimir")
        request.submit()
        request.reload()

        self.assertEqual(request.status, "Completado", request.report_json)
        self.assertEqual(self._leaks(), [])

        # El expediente tampoco guarda el correo en claro.
        self.assertFalse(request.emails)
        self.assertTrue(request.subject_hashes)

        # El consentimiento sigue probado, pero sin el correo ni la IP.
        records = frappe.get_all(
            consent_log.RECORD_DOCTYPE,
            filters={"reference_doctype": "CRM Lead", "reference_name": self.lead.name},
            fields=["email", "ip_address", "action", "channel"],
        )
        self.assertTrue(records)
        for record in records:
            self.assertTrue(record.email.startswith(consent_log.TOKEN_PREFIX))
            self.assertIsNone(record.ip_address)
        self.assertIn(("Revocado", "Supresión"), {(r.action, r.channel) for r in records})

        # Consultado por su correo, el titular figura como revocado y no
        # se lo puede volver a suscribir.
        self.assertEqual(consent_log.current_state(self.email), REVOKED)

        self.assertTrue(privacy.is_suppressed("CRM Lead", self.lead.name))
        self.assertIn("Hemos suprimido", request.response_text)

    def test_desasociar_conserva_el_contenido(self):
        request = self._request(subject_type="B2B", resolution="Desasociar")
        request.submit()

        content = frappe.db.get_value("Communication", self.communication.name, "content")
        self.assertNotIn(self.name, content)
        self.assertNotIn("8888", content)
        self.assertIn("Hola, soy", content)

    def test_conservar_requiere_habilitarlo(self):
        request = self._request(
            subject_type="B2B", resolution=privacy.RETAIN, legal_basis="Decreto 37554-JP, art. 3."
        )
        self.assertRaises(frappe.ValidationError, request.submit)

    def test_conservar_solo_revoca_la_publicidad(self):
        frappe.db.set_single_value("CORTEC Helpdesk Settings", "allow_professional_retention", 1)
        request = self._request(
            subject_type="B2B", resolution=privacy.RETAIN, legal_basis="Decreto 37554-JP, art. 3."
        )
        request.submit()

        self.assertEqual(frappe.db.get_value("CRM Lead", self.lead.name, "email"), self.email)
        self.assertEqual(consent_log.current_state(self.email), REVOKED)
        self.assertIn("Decreto 37554-JP, art. 3.", frappe.db.get_value(privacy.REQUEST_DOCTYPE, request.name, "response_text"))

    def test_revocacion_no_anonimiza(self):
        request = self._request(request_type="Revocación")
        request.submit()
        self.assertEqual(frappe.db.get_value("CRM Lead", self.lead.name, "email"), self.email)
        self.assertEqual(consent_log.current_state(self.email), REVOKED)

    def test_informacion_adicional_solo_una_vez(self):
        request = self._request()
        request_api.request_information(request.name)
        self.assertEqual(
            frappe.db.get_value(privacy.REQUEST_DOCTYPE, request.name, "status"), "Esperando información"
        )
        self.assertRaises(frappe.ValidationError, request_api.request_information, request.name)

    def test_el_expediente_no_se_borra(self):
        request = self._request()
        self.assertRaises(
            frappe.ValidationError, frappe.delete_doc, privacy.REQUEST_DOCTYPE, request.name, ignore_permissions=True
        )
