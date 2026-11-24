// Print Sections on Quotation / Sales Order: picking a Terms and Conditions
// template fills the page title (the template's Print Title, else its name) and
// the text. Both stay editable for this document; re-picking a template refills.
frappe.ui.form.on("Print Section", {
	terms_template(frm, cdt, cdn) {
		const row = locals[cdt][cdn];
		if (!row.terms_template) return;
		frappe.db.get_value("Terms and Conditions", row.terms_template, ["custom_print_title", "terms"]).then((r) => {
			const v = (r && r.message) || {};
			frappe.model.set_value(cdt, cdn, "title", v.custom_print_title || row.terms_template);
			frappe.model.set_value(cdt, cdn, "content", v.terms || "");
		});
	},
});
