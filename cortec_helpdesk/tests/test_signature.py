# Copyright (C) 2026 Corporación de Tecnología CORTEC S.R.L.
# SPDX-License-Identifier: AGPL-3.0-or-later
"""
bench --site <sitio de pruebas> run-tests --app cortec_helpdesk --module cortec_helpdesk.tests.test_signature
"""
from __future__ import annotations

import sys
import types
from unittest import mock

try:
    from frappe.tests import IntegrationTestCase as TestCase
except ImportError:  # Frappe v15
    from frappe.tests.utils import FrappeTestCase as TestCase

from cortec_helpdesk.overrides import signature

GENERAL = '<br><p class="signature">Firma general</p>'
EDITOR_VACIO = '<div class="ql-editor read-mode"><p><br></p></div>'


def _apps_ajenas(crm_signature=GENERAL, helpdesk_info=None):
    """
    CRM y Helpdesk simulados: las pruebas ejercitan la lógica propia, no
    dependen de tener esas apps (ni su base de datos) en el sitio de
    pruebas. Devuelve el diccionario para mock.patch.dict(sys.modules).
    """
    crm_api = types.ModuleType("crm.api")
    crm_api.get_user_signature = mock.Mock(return_value=crm_signature)

    hd_auth = types.ModuleType("helpdesk.api.auth")
    hd_auth.get_current_user_email_info = mock.Mock(
        return_value=helpdesk_info
        if helpdesk_info is not None
        else {
            "email_signature": "<p>Firma general</p>",
            "email": "agente@cortec.cr",
            "outgoing_emails": [{"email_account": "correo1", "email_id": "correo1@x.cr"}],
            "available_emails": [{"name": "correo1", "email_id": "correo1@x.cr"}],
        }
    )

    return {
        "crm": types.ModuleType("crm"),
        "crm.api": crm_api,
        "helpdesk": types.ModuleType("helpdesk"),
        "helpdesk.api": types.ModuleType("helpdesk.api"),
        "helpdesk.api.auth": hd_auth,
    }


def _campos(**valores):
    """Sustituto de _get_user_field que responde según el campo pedido."""
    return lambda fieldname: valores.get(fieldname)


class TestHayContenido(TestCase):
    """
    Un Text Editor vacío no guarda "" sino HTML en blanco. Compararlo con
    la cadena vacía tomaría una firma vacía como real y taparía a la general.
    """

    def test_vacios(self):
        for valor in (None, "", "   ", EDITOR_VACIO, "<p>&nbsp;</p>", "<p><br></p>"):
            self.assertFalse(signature._has_content(valor), repr(valor))

    def test_con_texto(self):
        self.assertTrue(signature._has_content("<p>Pierre Van der Laat</p>"))
        self.assertTrue(signature._has_content(EDITOR_VACIO.replace("<br>", "Ventas")))

    def test_un_logo_sin_texto_es_contenido(self):
        self.assertTrue(signature._has_content('<p><img src="/files/logo.png"></p>'))


class TestFirmaEnCRM(TestCase):
    def test_con_firma_propia_se_usa_esa_y_no_se_consulta_la_original(self):
        apps = _apps_ajenas()
        with mock.patch.dict(sys.modules, apps), mock.patch.object(
            signature, "_get_user_field", _campos(custom_firma_crm="<p>Ventas CORTEC</p>")
        ):
            resultado = signature.get_crm_signature()

        self.assertEqual(resultado, '<br><p class="signature"><p>Ventas CORTEC</p></p>')
        apps["crm.api"].get_user_signature.assert_not_called()

    def test_sin_firma_propia_delega_en_la_original(self):
        apps = _apps_ajenas()
        with mock.patch.dict(sys.modules, apps), mock.patch.object(
            signature, "_get_user_field", _campos()
        ):
            resultado = signature.get_crm_signature()

        self.assertEqual(resultado, GENERAL)
        apps["crm.api"].get_user_signature.assert_called_once()

    def test_el_editor_en_blanco_no_tapa_a_la_general(self):
        apps = _apps_ajenas()
        with mock.patch.dict(sys.modules, apps), mock.patch.object(
            signature, "_get_user_field", _campos(custom_firma_crm=EDITOR_VACIO)
        ):
            resultado = signature.get_crm_signature()

        self.assertEqual(resultado, GENERAL)

    def test_la_firma_de_helpdesk_no_aparece_en_crm(self):
        apps = _apps_ajenas()
        with mock.patch.dict(sys.modules, apps), mock.patch.object(
            signature, "_get_user_field", _campos(custom_firma_helpdesk="<p>Soporte</p>")
        ):
            resultado = signature.get_crm_signature()

        self.assertNotIn("Soporte", resultado)
        self.assertEqual(resultado, GENERAL)


class TestFirmaEnHelpdesk(TestCase):
    def test_con_firma_propia_solo_cambia_email_signature(self):
        apps = _apps_ajenas()
        original = apps["helpdesk.api.auth"].get_current_user_email_info.return_value
        esperado = dict(original, email_signature="<p>Soporte CORTEC</p>")

        with mock.patch.dict(sys.modules, apps), mock.patch.object(
            signature, "_get_user_field", _campos(custom_firma_helpdesk="<p>Soporte CORTEC</p>")
        ):
            resultado = signature.get_helpdesk_email_info()

        self.assertEqual(resultado, esperado)
        self.assertEqual(resultado["email"], "agente@cortec.cr")
        self.assertEqual(resultado["outgoing_emails"], original["outgoing_emails"])

    def test_sin_firma_propia_el_resultado_es_el_de_la_original(self):
        apps = _apps_ajenas()
        original = dict(apps["helpdesk.api.auth"].get_current_user_email_info.return_value)

        with mock.patch.dict(sys.modules, apps), mock.patch.object(
            signature, "_get_user_field", _campos()
        ):
            resultado = signature.get_helpdesk_email_info()

        self.assertEqual(resultado, original)

    def test_el_editor_en_blanco_no_tapa_a_la_general(self):
        apps = _apps_ajenas()
        with mock.patch.dict(sys.modules, apps), mock.patch.object(
            signature, "_get_user_field", _campos(custom_firma_helpdesk=EDITOR_VACIO)
        ):
            resultado = signature.get_helpdesk_email_info()

        self.assertEqual(resultado["email_signature"], "<p>Firma general</p>")

    def test_la_firma_de_crm_no_aparece_en_helpdesk(self):
        apps = _apps_ajenas()
        with mock.patch.dict(sys.modules, apps), mock.patch.object(
            signature, "_get_user_field", _campos(custom_firma_crm="<p>Ventas</p>")
        ):
            resultado = signature.get_helpdesk_email_info()

        self.assertNotIn("Ventas", resultado["email_signature"])
