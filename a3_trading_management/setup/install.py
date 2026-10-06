# Implements: the Item gate for serialised trailers, and the links that attach a
# job card, warranty or claim to a trailer serial (tasks 8 and 12); the Quality
# Inspection reference to a Work Order.
"""Custom fields A3 Trading adds to doctypes it does not own.

Created from code rather than a fixture because `create_custom_fields` is
idempotent and runs on every migrate, so a site that missed a fixture import still
ends up with the fields. Keeping them here also keeps the whole set in one place
where the reason for each is written down.
"""

import frappe
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields


SERIAL_LINK = {
	"fieldname": "custom_trailer_serial",
	"fieldtype": "Link",
	"options": "Trailer Serial",
	"label": "Trailer Serial",
	"insert_after": "naming_series",
	"search_index": 1,
	"description": "The identified trailer this record belongs to. This is how A3 "
	               "answers questions about a trailer years after it was sold.",
}

CUSTOM_FIELDS = {
	# Task 8 — only items ticked here mint a chassis number on their work order.
	"Item": [
		{
			"fieldname": "custom_is_trailer",
			"fieldtype": "Check",
			"label": "Is a Trailer",
			"insert_after": "is_stock_item",
			"description": "A work order for this item issues one chassis number and one "
			               "Trailer Serial per unit built.",
		}
	],
}

# Task 24 — the serial and chassis carried onto the selling documents and their
# print layouts, so the customer is invoiced for a specific identified trailer
# rather than a quantity.
SELLING_DOC_FIELDS = [
	{
		"fieldname": "custom_trailer_serial",
		"fieldtype": "Link",
		"options": "Trailer Serial",
		"label": "Trailer Serial",
		"insert_after": "customer_name",
		"search_index": 1,
	},
	{
		"fieldname": "custom_chassis_number",
		"fieldtype": "Data",
		"label": "Chassis Number",
		"insert_after": "custom_trailer_serial",
		"fetch_from": "custom_trailer_serial.chassis_number",
		"read_only": 1,
		# print_hide 0 so it lands on the printed note and invoice, which is the
		# point of the task.
		"print_hide": 0,
	},
]

for _dt in ("Sales Order", "Delivery Note", "Sales Invoice"):
	CUSTOM_FIELDS[_dt] = [dict(f) for f in SELLING_DOC_FIELDS]

# Task 18 — the hold that keeps a bill OUT of the ledger.
# ERPNext's own `on_hold` is a payment hold and can only be set AFTER submitting
# ("Purchase Invoice can be held after submitting"), so it cannot express "hold
# this before it posts". This is a separate, draft-stage gate.
CUSTOM_FIELDS["Purchase Invoice"] = [
	{
		"fieldname": "custom_approval_hold",
		"fieldtype": "Check",
		"label": "Held for Approval",
		"insert_after": "supplier_name",
		"description": "A bill on hold cannot be approved into the ledger.",
	},
	{
		"fieldname": "custom_hold_reason",
		"fieldtype": "Small Text",
		"label": "Hold Reason",
		"insert_after": "custom_approval_hold",
		"depends_on": "custom_approval_hold",
	},
]

# The order a finished trailer was reserved against.
CUSTOM_FIELDS["Trailer Serial"] = [
	{
		"fieldname": "sales_order",
		"fieldtype": "Link",
		"options": "Sales Order",
		"label": "Reserved For",
		"insert_after": "current_owner",
		"search_index": 1,
	}
]

# The Quality screen: an inspection passed by moving a work order past QC on the
# Manufacturing screen is recorded automatically, and says so.
CUSTOM_FIELDS["Quality Inspection"] = [
	{
		"fieldname": "custom_auto_recorded",
		"fieldtype": "Check",
		"label": "Recorded from Manufacturing",
		"insert_after": "manual_inspection",
		"read_only": 1,
		"description": "Created when the work order was moved past QC on the "
		               "Manufacturing screen without an inspection of its own.",
	}
]


# Labour tracking: who carries out each operation. The BOM holds the usual
# person for the step; the work order copies it and the floor can change it per
# order. allow_on_submit because a BOM is submitted before anyone assigns labour.
LABOUR_FIELDS = [
	{
		"fieldname": "custom_labour",
		"fieldtype": "Link",
		"options": "Employee",
		"label": "Labour",
		"insert_after": "workstation",
		"allow_on_submit": 1,
		"in_list_view": 1,
		"columns": 2,
	},
	{
		"fieldname": "custom_labour_name",
		"fieldtype": "Data",
		"label": "Labour Name",
		"insert_after": "custom_labour",
		"fetch_from": "custom_labour.employee_name",
		"read_only": 1,
		"allow_on_submit": 1,
	},
]
for _dt in ("BOM Operation", "Work Order Operation"):
	CUSTOM_FIELDS[_dt] = [dict(f) for f in LABOUR_FIELDS]

# The default checklist: every item can have a checklist of its own, and one
# checklist is marked the default, used for any item that has none (a new item,
# say). See api.quality._template_for.
CUSTOM_FIELDS["Quality Inspection Template"] = [
	{
		"fieldname": "custom_is_default",
		"fieldtype": "Check",
		"label": "Default Checklist",
		"insert_after": "quality_inspection_template_name",
		"description": "Used for any item that has no checklist of its own. Only one "
		               "checklist can be the default.",
	}
]

# Fields added earlier and since replaced; dropped on migrate.
RETIRED_FIELDS = [("Quality Inspection Template", "custom_default_for")]


def after_install():
	drop_retired_fields()
	setup_custom_fields()
	setup_quality_inspection_reference()
	setup_default_checklists()
	setup_deletion_workflow()


def after_migrate():
	drop_retired_fields()
	setup_custom_fields()
	setup_quality_inspection_reference()
	setup_default_checklists()
	setup_deletion_workflow()


# Two ready-made checklists, the first of them the default, so a new item is
# never inspected against nothing. Only touched while no checklist is the
# default, so a default chosen on the Quality screen is never overridden.
DEFAULT_CHECKLISTS = [
	{
		"name": "General Trailer Inspection",
		"checks": [
			("Chassis number embossed and legible", 0, 0, 0, "Matches the work order"),
			("Welds free of cracks and porosity", 0, 0, 0, "Visual check, all joints"),
			("Paint finish and coverage", 0, 0, 0, "No runs, bare spots or overspray"),
			("Brake system operation", 0, 0, 0, "Air holds, brakes apply and release"),
			("Lights and wiring harness", 0, 0, 0, "All lamps working, 7-pin tested"),
			("Tyre pressure (psi)", 1, 100, 120, ""),
			("Landing gear operation", 0, 0, 0, "Both speeds, full travel"),
			("King pin and twist locks secure", 0, 0, 0, "Torqued and pinned"),
		],
	},
	{
		"name": "General Material Inspection",
		"checks": [
			("Quantity matches delivery note", 0, 0, 0, "Counted against the note"),
			("Specification matches the order", 0, 0, 0, "Grade, size and part number"),
			("Free of visible damage", 0, 0, 0, "No dents, bends or cracks"),
			("Free of rust and corrosion", 0, 0, 0, ""),
			("Packaging and labelling intact", 0, 0, 0, ""),
			("Test certificate received", 0, 0, 0, "Where the order asks for one"),
		],
	},
]


def setup_deletion_workflow():
	"""Operator -> Finance -> Owner approval for deleting a posted invoice."""
	from a3_trading_management.setup.deletion_workflow import setup

	setup()


def drop_retired_fields():
	for doctype, fieldname in RETIRED_FIELDS:
		name = frappe.db.get_value("Custom Field", {"dt": doctype, "fieldname": fieldname}, "name")
		if name:
			frappe.delete_doc("Custom Field", name, ignore_permissions=True, force=True)


def setup_default_checklists():
	qit = "Quality Inspection Template"
	if not frappe.db.exists("DocType", qit) or not frappe.get_meta(qit).has_field("custom_is_default"):
		return
	if frappe.db.exists(qit, {"custom_is_default": 1}):
		return
	for i, spec in enumerate(DEFAULT_CHECKLISTS):
		if frappe.db.exists(qit, spec["name"]):
			if i == 0:
				frappe.db.set_value(qit, spec["name"], "custom_is_default", 1)
			continue
		doc = frappe.new_doc(qit)
		doc.quality_inspection_template_name = spec["name"]
		doc.custom_is_default = 1 if i == 0 else 0
		for check, numeric, lo, hi, expected in spec["checks"]:
			if not frappe.db.exists("Quality Inspection Parameter", check):
				frappe.get_doc({"doctype": "Quality Inspection Parameter", "parameter": check}).insert(
					ignore_permissions=True)
			doc.append("item_quality_inspection_parameter", {
				"specification": check, "numeric": numeric,
				"min_value": lo, "max_value": hi, "value": expected,
			})
		doc.insert(ignore_permissions=True)


def setup_quality_inspection_reference():
	"""Let a Quality Inspection reference a Work Order: A3 inspects a trailer at its
	QC stage, before any job card or Manufacture entry exists. The matching
	controller is integrations.quality_inspection.A3QualityInspection."""
	if not frappe.db.exists("DocType", "Quality Inspection"):
		return
	from frappe.custom.doctype.property_setter.property_setter import make_property_setter

	field = frappe.get_meta("Quality Inspection").get_field("reference_type")
	options = (field.options or "").split("\n")
	if "Work Order" in options:
		return
	make_property_setter(
		"Quality Inspection", "reference_type", "options", "\n".join(options + ["Work Order"]),
		"Text", validate_fields_for_doctype=False,
	)


def setup_custom_fields():
	"""Create the fields, skipping any doctype this site does not have.

	Skipping a doctype the site does not have keeps a partial install clean.
	"""
	present = {
		doctype: fields
		for doctype, fields in CUSTOM_FIELDS.items()
		if frappe.db.exists("DocType", doctype)
	}
	if present:
		create_custom_fields(present, ignore_validate=True)
