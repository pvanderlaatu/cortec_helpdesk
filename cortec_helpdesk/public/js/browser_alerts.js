// Copyright (C) 2025 Corporación de Tecnología CORTEC S.R.L.
// SPDX-License-Identifier: AGPL-3.0-or-later
//
// Alerta audible en /crm y /helpdesk (cortec_helpdesk.overrides.browser_alerts).
// Consulta periódicamente get_new_alerts y, si hay WhatsApp o correos nuevos,
// reproduce un tono (Web Audio, sin archivos) y muestra una notificación del
// navegador si el agente dio permiso.
//
// Los navegadores no permiten sonido hasta que el usuario interactúa con la
// página: cualquier clic lo desbloquea, y el botón flotante 🔕/🔔 además pide
// permiso de notificaciones y permite silenciar.

(function () {
	"use strict";

	if (window.__cortecBrowserAlerts) return;
	window.__cortecBrowserAlerts = true;

	const ENDPOINT = "/api/method/cortec_helpdesk.overrides.browser_alerts.get_new_alerts";
	const script = document.currentScript;
	const POLL_MS = Math.max(parseInt(script && script.dataset.poll, 10) || 10, 5) * 1000;

	const KEY_SINCE = "cortec_alerts:since";
	const KEY_USER = "cortec_alerts:user";
	const KEY_LOCK = "cortec_alerts:lock";
	const KEY_MUTED = "cortec_alerts:muted";

	// Tonos (frecuencias en Hz) por tipo de alerta.
	const TONES = {
		whatsapp: [880, 1320],
		email: [660, 880],
	};

	let audioCtx = null;
	let unlocked = false;
	let stopped = false;
	let timer = null;
	let button = null;

	// -----------------------------------------------------------------------
	// localStorage (puede no estar disponible: modo privado, bloqueos)
	// -----------------------------------------------------------------------

	function load(key) {
		try {
			return window.localStorage.getItem(key);
		} catch (e) {
			return null;
		}
	}

	function save(key, value) {
		try {
			if (value === null) window.localStorage.removeItem(key);
			else window.localStorage.setItem(key, value);
		} catch (e) {
			/* sin persistencia: se sigue funcionando en memoria */
		}
	}

	function isMuted() {
		return load(KEY_MUTED) === "1";
	}

	// -----------------------------------------------------------------------
	// Sonido
	// -----------------------------------------------------------------------

	function unlockAudio() {
		const AudioContext = window.AudioContext || window.webkitAudioContext;
		if (!AudioContext) return;
		if (!audioCtx) audioCtx = new AudioContext();
		if (audioCtx.state === "suspended") audioCtx.resume();
		unlocked = true;
		renderButton();
	}

	function playTone(kind) {
		if (!audioCtx || !unlocked || isMuted()) return;
		const notes = TONES[kind] || TONES.whatsapp;
		const start = audioCtx.currentTime + 0.02;

		notes.forEach((freq, i) => {
			const t = start + i * 0.18;
			const osc = audioCtx.createOscillator();
			const gain = audioCtx.createGain();
			osc.type = "sine";
			osc.frequency.value = freq;
			gain.gain.setValueAtTime(0.0001, t);
			gain.gain.exponentialRampToValueAtTime(0.35, t + 0.02);
			gain.gain.exponentialRampToValueAtTime(0.0001, t + 0.16);
			osc.connect(gain).connect(audioCtx.destination);
			osc.start(t);
			osc.stop(t + 0.17);
		});
	}

	// -----------------------------------------------------------------------
	// Notificaciones del navegador
	// -----------------------------------------------------------------------

	function canNotify() {
		return "Notification" in window && Notification.permission === "granted";
	}

	function showNotification(alert) {
		if (!canNotify() || isMuted()) return;
		try {
			const n = new Notification(alert.title, {
				body: alert.body,
				tag: alert.tag,
			});
			n.onclick = () => {
				window.focus();
				window.location.assign(alert.link);
				n.close();
			};
		} catch (e) {
			/* algunos navegadores móviles solo permiten notificaciones vía service worker */
		}
	}

	// -----------------------------------------------------------------------
	// Botón flotante
	// -----------------------------------------------------------------------

	function renderButton() {
		if (!button) return;
		let icon, title;
		if (!unlocked) {
			icon = "🔕";
			title = "Alertas CORTEC: haga clic para activar el sonido";
			button.style.opacity = "1";
		} else if (isMuted()) {
			icon = "🔕";
			title = "Alertas CORTEC silenciadas: clic para activar";
			button.style.opacity = "0.6";
		} else {
			icon = "🔔";
			title = "Alertas CORTEC activas: clic para silenciar";
			button.style.opacity = "0.6";
		}
		button.textContent = icon;
		button.title = title;
		button.setAttribute("aria-label", title);
	}

	function createButton() {
		button = document.createElement("button");
		button.type = "button";
		Object.assign(button.style, {
			position: "fixed",
			right: "16px",
			bottom: "16px",
			zIndex: "2147483000",
			width: "36px",
			height: "36px",
			borderRadius: "50%",
			border: "1px solid rgba(0,0,0,0.15)",
			background: "#fff",
			boxShadow: "0 2px 6px rgba(0,0,0,0.15)",
			fontSize: "18px",
			lineHeight: "1",
			cursor: "pointer",
			padding: "0",
		});
		button.addEventListener("mouseenter", () => (button.style.opacity = "1"));
		button.addEventListener("mouseleave", renderButton);
		button.addEventListener("click", (event) => {
			event.stopPropagation();
			if (!unlocked) {
				unlockAudio();
				save(KEY_MUTED, null);
				if ("Notification" in window && Notification.permission === "default") {
					Notification.requestPermission();
				}
				playTone("whatsapp");
			} else if (isMuted()) {
				save(KEY_MUTED, null);
				playTone("whatsapp");
			} else {
				save(KEY_MUTED, "1");
			}
			renderButton();
		});
		document.body.appendChild(button);
		renderButton();
	}

	// -----------------------------------------------------------------------
	// Consulta periódica
	// -----------------------------------------------------------------------

	// Con varias pestañas abiertas (p. ej. /crm y /helpdesk), solo una consulta
	// por intervalo: el candado y "since" se comparten vía localStorage.
	function acquireLock() {
		const now = Date.now();
		const last = parseInt(load(KEY_LOCK), 10) || 0;
		if (now - last < POLL_MS * 0.8) return false;
		save(KEY_LOCK, String(now));
		return true;
	}

	async function poll() {
		if (stopped || !acquireLock()) return;

		const body = new URLSearchParams();
		const since = load(KEY_SINCE);
		if (since) body.set("since", since);

		let response;
		try {
			response = await fetch(ENDPOINT, {
				method: "POST",
				credentials: "same-origin",
				headers: {
					Accept: "application/json",
					"X-Frappe-CSRF-Token": window.csrf_token || "",
				},
				body,
			});
		} catch (e) {
			return; // sin red: se reintenta en la próxima vuelta
		}

		if (response.status === 401 || response.status === 403) {
			stop();
			return;
		}
		if (!response.ok) return;

		let data;
		try {
			data = (await response.json()).message;
		} catch (e) {
			return;
		}
		if (!data || !data.enabled) {
			stop();
			return;
		}

		// Otro usuario en este navegador: empezar de cero sin avisos viejos.
		if (load(KEY_USER) !== data.user) {
			save(KEY_USER, data.user);
			save(KEY_SINCE, data.now);
			return;
		}
		save(KEY_SINCE, data.now);

		const alerts = data.alerts || [];
		if (!alerts.length) return;

		playTone(alerts.some((a) => a.kind === "whatsapp") ? "whatsapp" : "email");
		alerts.forEach(showNotification);
	}

	function schedule() {
		if (stopped) return;
		timer = setTimeout(async () => {
			await poll();
			schedule();
		}, POLL_MS);
	}

	function stop() {
		stopped = true;
		clearTimeout(timer);
		if (button) button.remove();
	}

	// -----------------------------------------------------------------------
	// Inicio
	// -----------------------------------------------------------------------

	function init() {
		createButton();

		// Cualquier interacción desbloquea el audio (requisito del navegador).
		// El botón se excluye: su propio clic desbloquea y pide permisos, y si
		// se desbloqueara aquí antes, ese mismo clic se tomaría como "silenciar".
		const unlockOnce = (event) => {
			if (button && button.contains(event.target)) return;
			unlockAudio();
			document.removeEventListener("pointerdown", unlockOnce, true);
			document.removeEventListener("keydown", unlockOnce, true);
		};
		document.addEventListener("pointerdown", unlockOnce, true);
		document.addEventListener("keydown", unlockOnce, true);

		// Al volver a la pestaña, consultar enseguida.
		document.addEventListener("visibilitychange", () => {
			if (document.visibilityState === "visible") poll();
		});

		poll().then(schedule);
	}

	if (document.readyState === "loading") {
		document.addEventListener("DOMContentLoaded", init);
	} else {
		init();
	}
})();
