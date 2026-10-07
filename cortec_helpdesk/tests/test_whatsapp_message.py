# Copyright (C) 2026 Corporación de Tecnología CORTEC S.R.L.
# SPDX-License-Identifier: AGPL-3.0-or-later
"""
bench --site <sitio de pruebas> run-tests --app cortec_helpdesk --module cortec_helpdesk.tests.test_whatsapp_message
"""
from __future__ import annotations

from unittest import mock

import frappe

try:
    from frappe.tests import IntegrationTestCase as TestCase
except ImportError:  # Frappe v15
    from frappe.tests.utils import FrappeTestCase as TestCase

from cortec_helpdesk.overrides import whatsapp_message
from cortec_helpdesk.overrides.whatsapp_message import CORTECWhatsAppMessage


def _doc(**kwargs) -> CORTECWhatsAppMessage:
    """
    Instancia sin pasar por la base de datos: estas pruebas ejercitan la
    lógica propia, no el ciclo de vida de Frappe.
    """
    doc = CORTECWhatsAppMessage.__new__(CORTECWhatsAppMessage)
    doc.__dict__.update(
        {"doctype": "WhatsApp Message", "content_type": "text", "attach": None, "message": ""}
    )
    doc.__dict__.update(kwargs)
    return doc


class TestNormalizacionDelMensaje(TestCase):
    """
    WhatsAppArea.vue del CRM hace `whatsapp.message.startsWith('/files/')`
    sin comprobar nulos: un mensaje sin texto deja la conversación en
    blanco. Y la ruta que el CRM guarda en `message` se le enviaba al
    cliente como pie de foto.
    """

    def test_mensaje_nulo_pasa_a_vacio(self):
        doc = _doc(message=None, content_type="image", attach="/private/files/x.jpeg")
        doc._normalize_message()
        self.assertEqual(doc.message, "")

    def test_la_ruta_del_adjunto_no_viaja_como_pie_de_foto(self):
        doc = _doc(
            message="/private/files/caja.jpeg",
            content_type="image",
            attach="/private/files/caja.jpeg",
        )
        doc._normalize_message()
        self.assertEqual(doc.message, "")

    def test_un_pie_de_foto_real_se_respeta(self):
        doc = _doc(
            message="Le adjunto la caja",
            content_type="image",
            attach="/private/files/caja.jpeg",
        )
        doc._normalize_message()
        self.assertEqual(doc.message, "Le adjunto la caja")


class TestSubidaDeAdjuntos(TestCase):
    """notify() sustituye el enlace por el media_id subido a Meta."""

    def setUp(self):
        patcher = mock.patch.object(
            whatsapp_message, "is_whatsapp_media_upload_enabled", return_value=True
        )
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_el_enlace_se_cambia_por_el_media_id(self):
        doc = _doc(content_type="image", attach="/private/files/caja.jpeg")
        data = {
            "messaging_product": "whatsapp",
            "image": {"link": f"{frappe.utils.get_url()}/private/files/caja.jpeg", "caption": "hola"},
        }

        with mock.patch.object(CORTECWhatsAppMessage, "_upload_media", return_value="MEDIA-1"), \
                mock.patch.object(whatsapp_message.WhatsAppMessage, "notify") as original:
            doc.notify(data)

        self.assertEqual(data["image"], {"id": "MEDIA-1", "caption": "hola"})
        original.assert_called_once()

    def test_un_mensaje_de_texto_no_se_toca(self):
        doc = _doc(content_type="text")
        data = {"messaging_product": "whatsapp", "text": {"body": "hola", "preview_url": True}}

        with mock.patch.object(CORTECWhatsAppMessage, "_upload_media") as upload, \
                mock.patch.object(whatsapp_message.WhatsAppMessage, "notify"):
            doc.notify(data)

        upload.assert_not_called()
        self.assertEqual(data["text"], {"body": "hola", "preview_url": True})

    def test_un_enlace_externo_lo_descarga_meta(self):
        doc = _doc(content_type="image", attach="https://otro.sitio/foto.jpg")
        data = {"image": {"link": "https://otro.sitio/foto.jpg"}}

        with mock.patch.object(CORTECWhatsAppMessage, "_upload_media") as upload, \
                mock.patch.object(whatsapp_message.WhatsAppMessage, "notify"):
            doc.notify(data)

        upload.assert_not_called()
        self.assertEqual(data["image"]["link"], "https://otro.sitio/foto.jpg")

    def test_si_la_subida_falla_no_se_envia_ni_se_publica_nada(self):
        doc = _doc(content_type="document", attach="/private/files/cotizacion.pdf")
        data = {"document": {"link": f"{frappe.utils.get_url()}/private/files/cotizacion.pdf"}}

        with mock.patch.object(
            CORTECWhatsAppMessage, "_upload_media", side_effect=frappe.ValidationError("Meta dijo que no")
        ), mock.patch.object(whatsapp_message.WhatsAppMessage, "notify") as original:
            with self.assertRaises(frappe.ValidationError):
                doc.notify(data)

        original.assert_not_called()
        self.assertIn("link", data["document"])

    def test_el_interruptor_apagado_deja_el_enlace(self):
        doc = _doc(content_type="image", attach="/private/files/caja.jpeg")
        data = {"image": {"link": f"{frappe.utils.get_url()}/private/files/caja.jpeg"}}

        with mock.patch.object(
            whatsapp_message, "is_whatsapp_media_upload_enabled", return_value=False
        ), mock.patch.object(CORTECWhatsAppMessage, "_upload_media") as upload, \
                mock.patch.object(whatsapp_message.WhatsAppMessage, "notify"):
            doc.notify(data)

        upload.assert_not_called()


class TestLimitesDeMeta(TestCase):
    def test_una_imagen_de_mas_de_5_mb_se_rechaza(self):
        with self.assertRaises(frappe.ValidationError):
            whatsapp_message._validate_size(b"x" * (6 * 1024 * 1024), "image", "grande.jpeg")

    def test_un_documento_de_6_mb_pasa(self):
        whatsapp_message._validate_size(b"x" * (6 * 1024 * 1024), "document", "cotizacion.pdf")
