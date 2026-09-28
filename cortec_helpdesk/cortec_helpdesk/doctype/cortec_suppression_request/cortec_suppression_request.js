// Copyright (C) 2026 Corporación de Tecnología CORTEC S.R.L.
// SPDX-License-Identifier: AGPL-3.0-or-later

const REQUEST_API =
	"cortec_helpdesk.cortec_helpdesk.doctype.cortec_suppression_request.cortec_suppression_request";
const RETAIN = "Conservar — dato profesional";

frappe.ui.form.on("CORTEC Suppression Request", {
	async refresh(frm) {
		await set_resolution_options(frm);

		if (frm.doc.docstatus === 0 && !frm.is_new()) {
			add_draft_buttons(frm);
		}

		if (
			frm.doc.docstatus === 1 &&
			["Completado", "Con errores"].includes(frm.doc.status) &&
			!frm.doc.response_sent_on
		) {
			frm.add_custom_button(__("Marcar respuesta enviada"), () =>
				frappe.confirm(
					__(
						"Se registrará la fecha de envío y se borrará el medio de notificación del titular. ¿Continuar?"
					),
					() => call(frm, "mark_response_sent")
				)
			);
		}

		if (frm.doc.status === "En proceso") {
			frm.dashboard.set_headline(
				__("La solicitud se está ejecutando en segundo plano. Recargue en unos minutos.")
			);
		}
	},

	resolution(frm) {
		if (frm.doc.resolution === RETAIN && !frm.doc.legal_basis) {
			frappe.db
				.get_single_value("CORTEC Helpdesk Settings", "professional_retention_basis")
				.then((basis) => basis && frm.set_value("legal_basis", basis));
		}
	},
});

async function set_resolution_options(frm) {
	// «Conservar» solo aparece si el administrador lo habilitó.
	const allowed = await frappe.db.get_single_value(
		"CORTEC Helpdesk Settings",
		"allow_professional_retention"
	);
	const options = ["", "Desasociar", "Suprimir"];
	if (allowed || frm.doc.resolution === RETAIN) options.push(RETAIN);
	frm.set_df_property("resolution", "options", options.join("\n"));
}

function add_draft_buttons(frm) {
	const waiting = frm.doc.status === "Esperando información";

	if (!waiting && frm.doc.status !== "No presentada") {
		frm.add_custom_button(__("Buscar datos"), () => {
			if (frm.is_dirty()) {
				frappe.msgprint(__("Guarde los cambios antes de buscar."));
				return;
			}
			call(frm, "search_data", (r) => {
				const c = r.message || {};
				frappe.msgprint({
					title: __("Datos encontrados"),
					message:
						__("Tipo sugerido: {0}", [c.sugerida]) +
						(c.empresas && c.empresas.length
							? "<br>" + __("Empresas: {0}", [c.empresas.join(", ")])
							: "") +
						"<br><br>" +
						__("Revise «Datos encontrados» antes de enviar la solicitud."),
				});
			});
		});
	}

	if (!frm.doc.info_requested_on) {
		frm.add_custom_button(
			__("Solicitar información adicional"),
			() =>
				frappe.confirm(
					__(
						"Solo se puede pedir una vez (Decreto 37554-JP, art. 19). El plazo de respuesta queda en pausa. ¿Continuar?"
					),
					() => call(frm, "request_information")
				),
			__("Información")
		);
	} else if (waiting) {
		frm.add_custom_button(
			__("Información recibida"),
			() => call(frm, "information_received"),
			__("Información")
		);
	}
}

function call(frm, method, callback) {
	frappe.call({
		method: `${REQUEST_API}.${method}`,
		args: { name: frm.doc.name },
		freeze: true,
		callback: (r) => {
			if (r.exc) return;
			frm.reload_doc();
			if (callback) callback(r);
		},
	});
}
