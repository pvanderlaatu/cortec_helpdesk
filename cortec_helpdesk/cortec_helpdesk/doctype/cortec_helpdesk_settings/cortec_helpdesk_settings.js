// Copyright (C) 2025 Corporación de Tecnología CORTEC S.R.L.
// SPDX-License-Identifier: AGPL-3.0-or-later

const TELEGRAM_API = "cortec_helpdesk.overrides.telegram";

frappe.ui.form.on("CORTEC Helpdesk Settings", {
	refresh(frm) {
		const only_outgoing_accounts = () => ({
			filters: { enable_outgoing: 1 },
		});

		frm.set_query("helpdesk_email_account", only_outgoing_accounts);
		frm.set_query("crm_email_account", only_outgoing_accounts);

		if (frm.doc.telegram_enabled) {
			frm.add_custom_button(
				__("Detectar chats"),
				() => detect_telegram_chats(frm),
				__("Telegram")
			);
			frm.add_custom_button(
				__("Enviar prueba"),
				() => send_telegram_test(frm),
				__("Telegram")
			);
		}
	},
});

function detect_telegram_chats(frm) {
	if (frm.is_dirty()) {
		frappe.msgprint(__("Guarde los cambios antes de detectar chats."));
		return;
	}

	frappe.call({
		method: `${TELEGRAM_API}.detect_telegram_chats`,
		freeze: true,
		callback: (r) => {
			const chats = r.message || [];
			if (!chats.length) {
				frappe.msgprint(
					__(
						"No hay chats nuevos. Pida al agente que abra el bot en Telegram y pulse /start, y vuelva a intentarlo."
					)
				);
				return;
			}
			show_chats_dialog(frm, chats);
		},
	});
}

function show_chats_dialog(frm, chats) {
	const linked = new Set((frm.doc.telegram_agents || []).map((row) => row.chat_id));

	const dialog = new frappe.ui.Dialog({
		title: __("Chats detectados"),
		fields: [
			{
				fieldname: "chat_id",
				fieldtype: "Select",
				label: __("Chat"),
				reqd: 1,
				options: chats
					.filter((chat) => !linked.has(chat.chat_id))
					.map((chat) => ({
						value: chat.chat_id,
						label: `${chat.name}${chat.username ? " (@" + chat.username + ")" : ""} — ${chat.chat_id}`,
					})),
			},
			{
				fieldname: "user",
				fieldtype: "Link",
				label: __("Agente"),
				options: "User",
				reqd: 1,
				get_query: () => ({ filters: { enabled: 1 } }),
			},
		],
		primary_action_label: __("Agregar"),
		primary_action(values) {
			frm.add_child("telegram_agents", {
				user: values.user,
				chat_id: values.chat_id,
				notify_whatsapp: 1,
				notify_email: 1,
			});
			frm.refresh_field("telegram_agents");
			dialog.hide();
			frm.save();
		},
	});

	if (!dialog.fields_dict.chat_id.df.options.length) {
		frappe.msgprint(__("Todos los chats detectados ya están vinculados."));
		return;
	}
	dialog.show();
}

function send_telegram_test(frm) {
	if (frm.is_dirty()) {
		frappe.msgprint(__("Guarde los cambios antes de enviar la prueba."));
		return;
	}

	frappe.call({
		method: `${TELEGRAM_API}.send_telegram_test`,
		freeze: true,
		callback: (r) => {
			const results = r.message || [];
			if (!results.length) {
				frappe.msgprint(__("No hay agentes en la tabla."));
				return;
			}
			const rows = results
				.map(
					(res) =>
						`<tr><td>${frappe.utils.escape_html(res.user)}</td><td>${
							res.ok ? "✅" : "❌ " + frappe.utils.escape_html(res.error || "")
						}</td></tr>`
				)
				.join("");
			frappe.msgprint({
				title: __("Resultado de la prueba"),
				message: `<table class="table table-bordered">${rows}</table>`,
			});
		},
	});
}
