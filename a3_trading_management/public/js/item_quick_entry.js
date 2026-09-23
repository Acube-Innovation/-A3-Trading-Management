// Pre-fill Item Code in the "New Item" dialog with the next number in the series.
//
// Item Code is a normal mandatory field, so the dialog already shows it — it just
// comes up empty. This fills it in; the user can accept it or type their own code
// over it, and a3_trading_management.integrations.item.before_naming() honours either.
//
// This has to be app-wide JS, not a form script: the dialog is built straight from
// the meta by frappe.ui.form.make_quick_entry(), which looks for
// `frappe.ui.form.<Doctype>QuickEntryForm` and uses it in place of the base class,
// long before any Item form script is evaluated.
frappe.ui.form.ItemQuickEntryForm = class ItemQuickEntryForm extends frappe.ui.form.QuickEntryForm {
	setup() {
		// Fetched before super.setup() so the value is in hand by the time the dialog
		// renders, rather than appearing in the field a moment later.
		return frappe
			.xcall("a3_trading_management.integrations.item.next_item_code")
			.catch(() => null)
			.then((code) => {
				this.next_item_code = code;
				return super.setup();
			});
	}

	render_dialog() {
		super.render_dialog();

		// Set after render: super.render_dialog() ends in set_defaults(), which seeds
		// the fields from the new doc and would otherwise blank this again.
		if (this.next_item_code && !this.dialog.get_value("item_code")) {
			this.dialog.set_value("item_code", this.next_item_code);
		}
	}
};
