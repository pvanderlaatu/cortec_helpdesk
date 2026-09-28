# Copyright (C) 2026 Corporación de Tecnología CORTEC S.R.L.
# SPDX-License-Identifier: AGPL-3.0-or-later
"""
bench --site <sitio de pruebas> run-tests --app cortec_helpdesk --module cortec_helpdesk.tests.test_consent_log
"""
from __future__ import annotations

import frappe

try:
    from frappe.tests import IntegrationTestCase as TestCase
except ImportError:  # Frappe v15
    from frappe.tests.utils import FrappeTestCase as TestCase

from cortec_helpdesk import api, consent_log
from cortec_helpdesk.consent_log import (
    GRANTED,
    PRIVACY_AGREEMENT,
    PROMOTIONS_AGREEMENT,
    REVOKED,
    register_consent,
)
from cortec_helpdesk.cortec_helpdesk.doctype.cortec_helpdesk_settings.cortec_helpdesk_settings import (
    get_promotions_email_group,
)


def _email(tag: str) -> str:
    return f"{tag}-{frappe.generate_hash(length=8)}@example.com"


def _lead(email: str | None) -> str:
    return (
        frappe.get_doc({"doctype": "CRM Lead", "first_name": "Prueba", "email": email})
        .insert(ignore_permissions=True)
        .name
    )


class TestConsentLog(TestCase):
    def setUp(self):
        consent_log.ensure_default_agreements()
        group = get_promotions_email_group()
        if not frappe.db.exists("Email Group", group):
            frappe.get_doc({"doctype": "Email Group", "title": group}).insert(
                ignore_permissions=True
            )

    def tearDown(self):
        frappe.db.rollback()

    # -- inmutabilidad -------------------------------------------------------

    def test_el_registro_no_se_modifica_cancela_ni_borra(self):
        email = _email("inmutable")
        name = register_consent(PROMOTIONS_AGREEMENT, GRANTED, email, channel="Teléfono")
        doc = frappe.get_doc(consent_log.RECORD_DOCTYPE, name)

        blocked = (frappe.ValidationError, frappe.PermissionError)

        doc.notes = "cambio"
        self.assertRaises(blocked, doc.save, ignore_permissions=True)
        self.assertRaises(blocked, doc.cancel)
        self.assertRaises(
            blocked,
            frappe.delete_doc,
            consent_log.RECORD_DOCTYPE,
            name,
            ignore_permissions=True,
        )

    def test_guarda_version_y_texto_del_acuerdo(self):
        name = register_consent(
            PRIVACY_AGREEMENT, GRANTED, _email("texto"), channel="Formulario web"
        )
        agreement = frappe.get_doc(consent_log.AGREEMENT_DOCTYPE, PRIVACY_AGREEMENT)
        record = frappe.get_doc(consent_log.RECORD_DOCTYPE, name)
        self.assertEqual(record.agreement_version, agreement.version)
        self.assertEqual(record.agreement_text_snapshot, agreement.agreement_text)

    def test_cambiar_el_texto_sube_la_version(self):
        agreement = frappe.get_doc(consent_log.AGREEMENT_DOCTYPE, PROMOTIONS_AGREEMENT)
        before = agreement.version
        agreement.agreement_text = f"<p>Texto nuevo {frappe.generate_hash(length=6)}</p>"
        agreement.save(ignore_permissions=True)
        self.assertEqual(agreement.version, before + 1)

    # -- idempotencia --------------------------------------------------------

    def test_misma_referencia_de_origen_no_duplica(self):
        email = _email("idem")
        first = register_consent(
            PROMOTIONS_AGREEMENT, GRANTED, email, channel="Bitrix24", source_reference="x-1"
        )
        second = register_consent(
            PROMOTIONS_AGREEMENT, GRANTED, email, channel="Bitrix24", source_reference="x-1"
        )
        self.assertEqual(first, second)

    # -- campos derivados ----------------------------------------------------

    def test_otorgar_y_revocar_se_refleja_en_lead_y_contacto(self):
        email = _email("derivado")
        lead = _lead(email)
        contact = frappe.get_doc(
            {
                "doctype": "Contact",
                "first_name": "Prueba",
                "email_ids": [{"email_id": email, "is_primary": 1}],
            }
        ).insert(ignore_permissions=True)

        register_consent(PROMOTIONS_AGREEMENT, GRANTED, email, channel="Teléfono")
        self.assertEqual(frappe.db.get_value("CRM Lead", lead, "custom_acepta_promociones"), 1)
        self.assertEqual(
            frappe.db.get_value("Contact", contact.name, "custom_acepta_promociones"), 1
        )

        register_consent(PROMOTIONS_AGREEMENT, REVOKED, email, channel="Teléfono")
        self.assertEqual(frappe.db.get_value("CRM Lead", lead, "custom_acepta_promociones"), 0)
        self.assertEqual(
            frappe.db.get_value("Contact", contact.name, "custom_acepta_promociones"), 0
        )
        self.assertTrue(
            frappe.db.get_value("CRM Lead", lead, "custom_promociones_origen").startswith(REVOKED)
        )

    def test_contacto_nuevo_hereda_el_consentimiento_del_correo(self):
        email = _email("hereda")
        register_consent(PROMOTIONS_AGREEMENT, GRANTED, email, channel="Formulario web")
        contact = frappe.get_doc(
            {
                "doctype": "Contact",
                "first_name": "Convertido",
                "email_ids": [{"email_id": email, "is_primary": 1}],
            }
        ).insert(ignore_permissions=True)
        self.assertEqual(contact.custom_acepta_promociones, 1)

    def test_lead_sin_correo_por_referencia(self):
        lead = _lead(None)
        register_consent(
            PROMOTIONS_AGREEMENT, GRANTED, None, channel="Teléfono", reference=("CRM Lead", lead)
        )
        self.assertEqual(frappe.db.get_value("CRM Lead", lead, "custom_acepta_promociones"), 1)

    def test_marcar_a_mano_sin_registro_falla(self):
        lead = frappe.get_doc("CRM Lead", _lead(_email("manual")))
        lead.custom_acepta_promociones = 1
        lead.custom_promociones_origen = "Me lo dijo por teléfono"
        self.assertRaises(frappe.ValidationError, lead.save, ignore_permissions=True)

    def test_desmarcar_a_mano_con_registro_falla(self):
        email = _email("desmarcar")
        lead_name = _lead(email)
        register_consent(PROMOTIONS_AGREEMENT, GRANTED, email, channel="Teléfono")
        lead = frappe.get_doc("CRM Lead", lead_name)
        lead.custom_acepta_promociones = 0
        self.assertRaises(frappe.ValidationError, lead.save, ignore_permissions=True)

    # -- lista promocional ---------------------------------------------------

    def test_otorgar_suscribe_y_la_baja_revoca(self):
        email = _email("lista")
        group = get_promotions_email_group()
        register_consent(PROMOTIONS_AGREEMENT, GRANTED, email, channel="Formulario web")

        member = frappe.get_doc("Email Group Member", {"email_group": group, "email": email})
        self.assertEqual(member.unsubscribed, 0)

        member.unsubscribed = 1
        member.save(ignore_permissions=True)
        self.assertEqual(consent_log.current_state(email), REVOKED)

    def test_conciliacion_registra_bajas_escritas_directo(self):
        email = _email("conciliacion")
        group = get_promotions_email_group()
        register_consent(
            PROMOTIONS_AGREEMENT,
            GRANTED,
            email,
            channel="Formulario web",
            consent_datetime="2026-01-01 00:00:00",
        )
        member = frappe.db.get_value("Email Group Member", {"email_group": group, "email": email})
        frappe.db.set_value("Email Group Member", member, "unsubscribed", 1)

        consent_log.reconcile_unsubscribes()
        self.assertEqual(consent_log.current_state(email), REVOKED)

    def test_alta_en_lista_sin_consentimiento_falla(self):
        member = frappe.get_doc(
            {
                "doctype": "Email Group Member",
                "email_group": get_promotions_email_group(),
                "email": _email("sin"),
            }
        )
        self.assertRaises(frappe.ValidationError, member.insert, ignore_permissions=True)

    # -- formulario web ------------------------------------------------------

    def test_formulario_sin_politica_no_crea_lead(self):
        email = _email("sinpolitica")
        self.assertRaises(
            frappe.ValidationError,
            api.web_lead_intake,
            lead={"first_name": "Web", "email": email},
            consents=[{"agreement": PROMOTIONS_AGREEMENT, "accepted": True}],
        )
        self.assertFalse(frappe.db.exists("CRM Lead", {"email": email}))

    def test_formulario_crea_lead_y_registros(self):
        email = _email("web")
        result = api.web_lead_intake(
            lead={"first_name": "Web", "email": email, "custom_acepta_promociones": 1},
            consents=[
                {"agreement": PRIVACY_AGREEMENT, "accepted": True},
                {"agreement": PROMOTIONS_AGREEMENT, "accepted": True},
            ],
            ip="190.113.106.79",
            page_url="https://tecnocr.net/contacto",
            submission_id="sub-1",
        )
        self.assertEqual(len(result["consents"]), 2)

        lead = frappe.get_doc("CRM Lead", result["lead"])
        self.assertEqual(lead.custom_acepta_promociones, 1)
        self.assertIn("190.113.106.79", lead.custom_promociones_origen)
        self.assertIn("Formulario web", lead.custom_consentimiento)

        again = api.web_lead_intake(
            lead={"first_name": "Web", "email": email},
            consents=[{"agreement": PRIVACY_AGREEMENT, "accepted": True}],
            submission_id="sub-1",
        )
        self.assertEqual(again["lead"], result["lead"])
        self.assertTrue(again["duplicate"])
