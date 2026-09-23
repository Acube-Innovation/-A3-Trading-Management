// Pre-fill Item Code with the next number in the series on the full Item form.
// Same suggestion the "New Item" dialog shows (see item_quick_entry.js) — typing
// over it is fine, a3_trading_management.integrations.item.before_naming() keeps a real code.
frappe.ui.form.on("Item", {
	onload(frm) {
		// Variants get their code from the template's attributes, not the series.
		if (!frm.is_new() || frm.doc.item_code || frm.doc.variant_of) return;

		frappe.xcall("a3_trading_management.integrations.item.next_item_code").then((code) => {
			// Re-check: the user may have typed, saved or navigated while the call was out.
			if (!code || !frm.is_new() || frm.doc.item_code) return;

			// Assigned on the doc instead of through frm.set_value() deliberately.
			// set_value fires ERPNext's own `item_code` handler, which copies the code
			// into Item Name whenever that field is empty (item.js:275) — and a
			// suggested number is not a name for the Item.
			frm.doc.item_code = code;
			frm.refresh_field("item_code");
		});
	},
});
