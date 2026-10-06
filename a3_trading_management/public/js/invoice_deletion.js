// "Request Deletion" on posted Sales and Purchase Invoices. The button only raises
// an Invoice Deletion Request; Finance and then the Owner approve it, and the
// Owner's approval is what deletes the invoice (api/invoice_delete.py).
(function () {
	if (window.a3_invoice_deletion) return;
	window.a3_invoice_deletion = true;

	const API = "a3_trading_management.api.invoice_delete.";

	function list(title, rows, colour) {
		if (!rows || !rows.length) return "";
		return `<div style="margin-bottom:10px"><b style="color:${colour}">${title}</b><ul style="margin:4px 0 0 18px">`
			+ rows.map((r) => `<li>${frappe.utils.escape_html(r)}</li>`).join("") + "</ul></div>";
	}

	function open_dialog(frm) {
		frappe.call(API + "preview_deletion", { doctype: frm.doctype, name: frm.doc.name }).then((r) => {
			const impact = r.message || {};
			if (impact.open_request) {
				frappe.set_route("Form", "Invoice Deletion Request", impact.open_request.name);
				return;
			}
			const blocked = (impact.blockers || []).length > 0;
			const d = new frappe.ui.Dialog({
				title: __("Request deletion of {0}", [frm.doc.name]),
				size: "large",
				fields: [
					{ fieldtype: "HTML", fieldname: "impact", options:
						list(__("Blocked — fix these first"), impact.blockers, "#b3261e")
						+ list(__("Will be removed"), impact.removes, "#1f2430")
						+ list(__("Will be updated"), impact.updates, "#1f2430")
						+ list(__("Please note"), impact.warnings, "#8a5a00")
						+ `<p class="text-muted">${__("Nothing is deleted now. The request goes to Finance, then to the Owner; the deletion runs when the Owner approves.")}</p>` },
					{ fieldtype: "Small Text", fieldname: "reason", label: __("Reason"), reqd: !blocked, hidden: blocked },
				],
				primary_action_label: blocked ? __("Close") : __("Send to Finance"),
				primary_action(values) {
					if (blocked) return d.hide();
					frappe.call(API + "request_deletion", { doctype: frm.doctype, name: frm.doc.name, reason: values.reason })
						.then((res) => {
							d.hide();
							frappe.show_alert({ message: __("Deletion request {0} sent to Finance", [res.message.name]), indicator: "orange" });
							frappe.set_route("Form", "Invoice Deletion Request", res.message.name);
						});
				},
			});
			d.show();
		});
	}

	function refresh(frm) {
		if (frm.is_new() || frm.doc.docstatus === 0) return;
		frappe.db.get_value("Invoice Deletion Request",
			{ invoice_type: frm.doctype, invoice: frm.doc.name, workflow_state: ["in", ["Draft", "Pending Finance", "Pending Owner"]] },
			["name", "workflow_state"]).then((r) => {
			const req = r && r.message;
			if (req && req.name) {
				frm.dashboard.add_comment(__("Deletion requested: {0} — {1}",
					[`<a href="/app/invoice-deletion-request/${req.name}">${req.name}</a>`, req.workflow_state]), "orange", true);
			}
		});
		if (frappe.user.has_role("Operator") || frappe.user.has_role("System Manager")) {
			frm.add_custom_button(__("Request Deletion"), () => open_dialog(frm));
		}
	}

	["Sales Invoice", "Purchase Invoice"].forEach((dt) => frappe.ui.form.on(dt, { refresh }));
})();
