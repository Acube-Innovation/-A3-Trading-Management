"""25 Nov 2026 (user): a purchase invoice line is in one of the item's own units
(stock unit, default purchase unit, or its UOM conversion table), as on the Desk
form. Packing Type, the inner packing and the pieces-per-pack fields are gone:
their custom fields (and columns) on Purchase Invoice Item, the Packing Type
records and the DocType itself. No bill line used them when this ran.

27 Nov 2026: re-run (#2). A site was left with the DocType gone but
Purchase Invoice Item-custom_packing_type still linking to it ("Missing
DocType" on opening a Purchase Invoice). Also removes any other custom field,
on any DocType, that still links to Packing Type."""

import frappe

DT = "Purchase Invoice Item"
FIELDS = ("custom_packing_type", "custom_inner_packing", "custom_inner_per_pack", "custom_pcs_per_unit")


def execute():
	targets = {(DT, f) for f in FIELDS}
	targets |= {
		(r.dt, r.fieldname)
		for r in frappe.get_all("Custom Field", filters={"fieldtype": "Link", "options": "Packing Type"}, fields=["dt", "fieldname"])
	}

	for dt, fieldname in sorted(targets):
		name = frappe.db.get_value("Custom Field", {"dt": dt, "fieldname": fieldname})
		if name:
			frappe.delete_doc("Custom Field", name, force=1, ignore_permissions=True)
		frappe.db.delete("Property Setter", {"doc_type": dt, "field_name": fieldname})
		if frappe.db.table_exists(dt) and frappe.db.has_column(dt, fieldname):
			frappe.db.sql_ddl(f"alter table `tab{dt}` drop column `{fieldname}`")
		frappe.clear_cache(doctype=dt)

	if frappe.db.exists("DocType", "Packing Type"):
		frappe.db.delete("Packing Type")
		frappe.delete_doc("DocType", "Packing Type", force=1, ignore_permissions=True, ignore_missing=True)
	# Deleting a DocType leaves its table behind.
	frappe.db.sql_ddl("drop table if exists `tabPacking Type`")
