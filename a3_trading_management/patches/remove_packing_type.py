"""25 Nov 2026 (user): a purchase invoice line is in one of the item's own units
(stock unit, default purchase unit, or its UOM conversion table), as on the Desk
form. Packing Type, the inner packing and the pieces-per-pack fields are gone:
their custom fields (and columns) on Purchase Invoice Item, the Packing Type
records and the DocType itself. No bill line used them when this ran."""

import frappe

FIELDS = ("custom_packing_type", "custom_inner_packing", "custom_inner_per_pack", "custom_pcs_per_unit")


def execute():
	for fieldname in FIELDS:
		name = frappe.db.get_value("Custom Field", {"dt": "Purchase Invoice Item", "fieldname": fieldname})
		if name:
			frappe.delete_doc("Custom Field", name, force=1, ignore_permissions=True)
		if frappe.db.has_column("Purchase Invoice Item", fieldname):
			frappe.db.sql_ddl(f"alter table `tabPurchase Invoice Item` drop column `{fieldname}`")
	frappe.clear_cache(doctype="Purchase Invoice Item")

	if frappe.db.exists("DocType", "Packing Type"):
		frappe.db.delete("Packing Type")
		frappe.delete_doc("DocType", "Packing Type", force=1, ignore_permissions=True, ignore_missing=True)
	# Deleting a DocType leaves its table behind.
	frappe.db.sql_ddl("drop table if exists `tabPacking Type`")
