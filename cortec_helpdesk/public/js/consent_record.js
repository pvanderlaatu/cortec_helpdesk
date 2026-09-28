// Copyright (C) 2026 Corporación de Tecnología CORTEC S.R.L.
// SPDX-License-Identifier: AGPL-3.0-or-later
//
// Botones «Registrar consentimiento» y «Ver consentimientos» en CRM Lead
// y Contact. Los campos de consentimiento del documento son de solo
// lectura: se derivan de CORTEC Consent Record (consent_log.py).

(() => {
	const MANUAL_CHANNELS = ["Teléfono", "Presencial", "Correo", "Otro"];

	function open_dialog(frm) {
		const dialog = new frappe.ui.Dialog({
			title: __("Registrar consentimiento"),
			fields: [
				{
					fieldname: "agreement",
					fieldtype: "Link",
					label: __("Acuerdo"),
					options: "CORTEC User Agreement",
					reqd: 1,
					get_query: () => ({ filters: { is_active: 1 } }),
				},
				{
					fieldname: "action",
					fieldtype: "Select",
					label: __("Acción"),
					options: "Otorgado\nRevocado",
					default: "Otorgado",
					reqd: 1,
				},
				{
					fieldname: "channel",
					fieldtype: "Select",
					label: __("Canal"),
					options: MANUAL_CHANNELS.join("\n"),
					reqd: 1,
				},
				{
					fieldname: "notes",
					fieldtype: "Small Text",
					label: __("Evidencia"),
					description: __(
						"Cómo se obtuvo: quién lo dijo, qué texto se le leyó, dónde está la grabación o el correo."
					),
					reqd: 1,
				},
			],
			primary_action_label: __("Registrar"),
			primary_action(values) {
				frappe.call({
					method: "cortec_helpdesk.api.register_manual_consent",
					args: { doctype: frm.doctype, name: frm.doc.name, ...values },
					freeze: true,
					callback: (r) => {
						if (!r.exc) {
							dialog.hide();
							frappe.show_alert({
								message: __("Consentimiento registrado: {0}", [r.message.record]),
								indicator: "green",
							});
							frm.reload_doc();
						}
					},
				});
			},
		});
		dialog.show();
	}

	const handlers = {
		refresh(frm) {
			if (frm.is_new()) return;

			frm.add_custom_button(
				__("Registrar consentimiento"),
				() => open_dialog(frm),
				__("Consentimientos")
			);
			frm.add_custom_button(
				__("Ver consentimientos"),
				() => {
					// Por correo, que es como se une el Lead con el Contact
					// que nace de él; por documento si no tiene correo.
					const email = frm.doc.email || frm.doc.email_id;
					frappe.set_route(
						"List",
						"CORTEC Consent Record",
						email ? { email: email.toLowerCase() } : { reference_name: frm.doc.name }
					);
				},
				__("Consentimientos")
			);
		},
	};

	frappe.ui.form.on("CRM Lead", handlers);
	frappe.ui.form.on("Contact", handlers);
})();
