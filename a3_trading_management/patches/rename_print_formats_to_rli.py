"""The Quality Inspection and Delivery Note print formats were first shipped as
"A3 ..."; they are Redlines (RLI) formats. Rename them in place before the
fixtures sync, so a site that already has them ends up with one of each, under
the new name, rather than both."""

import frappe

RENAMES = {
	"A3 Quality Inspection": "RLI Quality Inspection",
	"A3 Delivery Note": "RLI Delivery Note",
}


def execute():
	for old, new in RENAMES.items():
		if frappe.db.exists("Print Format", old) and not frappe.db.exists("Print Format", new):
			frappe.rename_doc("Print Format", old, new, force=True)
		# The doctype's default points at the format by name (a Data value, not a link).
		frappe.db.set_value("Property Setter", {"property": "default_print_format", "value": old}, "value", new)
