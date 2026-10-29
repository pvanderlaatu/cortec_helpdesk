# Copyright (C) 2026 Corporación de Tecnología CORTEC S.R.L.
# SPDX-License-Identifier: AGPL-3.0-or-later
"""
bench --site <sitio de pruebas> run-tests --app cortec_helpdesk --module cortec_helpdesk.tests.test_alert_sound
"""
from __future__ import annotations

from unittest import mock

import frappe

try:
    from frappe.tests import IntegrationTestCase as TestCase
except ImportError:  # Frappe v15
    from frappe.tests.utils import FrappeTestCase as TestCase

from cortec_helpdesk.overrides import alert_sound


class _Usuario:
    """User mínimo: solo lo que validate_alert_sounds consulta."""

    def __init__(self, **campos):
        self._campos = campos
        self.cambiados = set(campos)

    def get(self, fieldname):
        return self._campos.get(fieldname)

    def has_value_changed(self, fieldname):
        return fieldname in self.cambiados


class TestExtension(TestCase):
    def test_permitidas(self):
        for url in ("/private/files/ding.mp3", "/files/Ding.WAV", "/files/a.ogg?x=1"):
            self.assertTrue(alert_sound._has_allowed_extension(url), url)

    def test_no_permitidas(self):
        for url in ("/files/a.exe", "/files/a.mp3.html", "/files/a", "/files/a.m4a"):
            self.assertFalse(alert_sound._has_allowed_extension(url), url)


class TestValidacion(TestCase):
    def _validar(self, usuario, size=None):
        with mock.patch.object(frappe.db, "get_value", return_value=size):
            with mock.patch.object(frappe, "get_meta") as meta:
                meta.return_value.get_label.return_value = "Sonido"
                alert_sound.validate_alert_sounds(usuario)

    def test_audio_valido_pasa(self):
        self._validar(_Usuario(custom_sonido_whatsapp="/private/files/ding.mp3"), size=50_000)

    def test_campo_vacio_pasa(self):
        self._validar(_Usuario(custom_sonido_whatsapp=None))

    def test_otro_tipo_de_archivo_se_rechaza(self):
        with self.assertRaises(frappe.ValidationError):
            self._validar(_Usuario(custom_sonido_correo="/files/foto.png"))

    def test_archivo_demasiado_grande_se_rechaza(self):
        with self.assertRaises(frappe.ValidationError):
            self._validar(
                _Usuario(custom_sonido_whatsapp="/files/largo.mp3"),
                size=alert_sound.MAX_SOUND_BYTES + 1,
            )

    def test_un_valor_que_no_cambio_no_se_revalida(self):
        usuario = _Usuario(custom_sonido_whatsapp="/files/viejo.exe")
        usuario.cambiados = set()
        self._validar(usuario)


class TestSonidosDelUsuario(TestCase):
    def test_devuelve_la_ruta_de_cada_tipo(self):
        valores = {
            "custom_sonido_whatsapp": "/private/files/a.mp3",
            "custom_sonido_correo": None,
        }
        with mock.patch.object(frappe.db, "get_value", return_value=valores):
            sonidos = alert_sound.get_user_sounds("agente@cortec.cr")

        self.assertEqual(sonidos, {"whatsapp": "/private/files/a.mp3", "email": None})

    def test_ignora_un_valor_que_no_es_audio(self):
        valores = {
            "custom_sonido_whatsapp": "/files/a.html",
            "custom_sonido_correo": "/files/b.wav",
        }
        with mock.patch.object(frappe.db, "get_value", return_value=valores):
            sonidos = alert_sound.get_user_sounds("agente@cortec.cr")

        self.assertEqual(sonidos, {"whatsapp": None, "email": "/files/b.wav"})

    def test_usuario_sin_registro(self):
        with mock.patch.object(frappe.db, "get_value", return_value=None):
            sonidos = alert_sound.get_user_sounds("nadie@cortec.cr")

        self.assertEqual(sonidos, {"whatsapp": None, "email": None})
