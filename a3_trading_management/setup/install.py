# Implements: the Item gate for serialised trailers (task 8).
"""Custom fields A3 Trading adds to doctypes it does not own.

Created from code rather than a fixture because `create_custom_fields` is
idempotent and runs on every migrate, so a site that missed a fixture import still
ends up with the fields. Keeping them here also keeps the whole set in one place
where the reason for each is written down.
"""

import frappe
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields


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
