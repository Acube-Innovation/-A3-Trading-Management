# Implements: the Item gate for serialised trailers, and the links that attach a
# job card, warranty or claim to a trailer serial (tasks 8 and 12).
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


def after_install():
	setup_custom_fields()


def after_migrate():
	setup_custom_fields()


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
