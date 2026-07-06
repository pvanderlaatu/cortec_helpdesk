// Copyright (C) 2025 Corporación de Tecnología CORTEC S.R.L.
// SPDX-License-Identifier: AGPL-3.0-or-later

frappe.ui.form.on("CORTEC Helpdesk Settings", {
	refresh(frm) {
		const only_outgoing_accounts = () => ({
			filters: { enable_outgoing: 1 },
		});

		frm.set_query("helpdesk_email_account", only_outgoing_accounts);
		frm.set_query("crm_email_account", only_outgoing_accounts);
	},
});
