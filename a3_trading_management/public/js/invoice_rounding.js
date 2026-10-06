// New invoices start with "Disable Rounded Total" ticked. The Property Setter
// default alone misses invoices mapped from an order, receipt or delivery note
// (they copy the source's value) and browsers holding a cached Sales Invoice meta.
["Sales Invoice", "Purchase Invoice"].forEach((doctype) => {
	frappe.ui.form.on(doctype, {
		onload(frm) {
			if (frm.is_new() && !frm.doc.disable_rounded_total) {
				frm.set_value("disable_rounded_total", 1);
			}
		},
	});
});
