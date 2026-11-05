# Implements: the trailer serial record that replaces the vehicle register (task 7).
"""Trailer Serial — how A3 identifies one physical trailer.

A3 sells trailers, so the thing being tracked is a unit with a chassis number, not
a vehicle with a plate and a driver. This record is what job cards, inspections,
warranty and claims hang off, and it is what makes stock a list of identified
units rather than a quantity.

The chassis number is stamped once, when the work order is created, and is then
immutable: it is embossed into the metal, so the record must not be able to drift
away from the trailer standing in the yard.
"""

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.model.naming import make_autoname

SERIAL_SERIES = "TRL-.#####"

# Set once and then frozen — these identify the physical unit.
IMMUTABLE_FIELDS = ("chassis_number", "work_order", "serial_no")


class TrailerSerial(Document):
	def before_insert(self):
		"""A trailer entered by hand (no work order): take the chassis number off
		its plate, or issue the next one. A work order's serials arrive with their
		number already issued by serial_control."""
		if self.work_order:
			return
		from a3_trading_management.a3_trading_management.doctype.chassis_numbering_rule.chassis_numbering_rule import (
			get_rule,
		)

		rule = get_rule()
		self.chassis_number = (self.chassis_number or "").strip().upper() or None
		if not self.chassis_number:
			self.chassis_number = rule.issue_next()
			return
		if frappe.db.exists("Trailer Serial", {"chassis_number": self.chassis_number}):
			frappe.throw(_("Chassis number {0} is already on another trailer.").format(self.chassis_number))
		rule.advance_past(self.chassis_number)

	def autoname(self):
		if not self.serial_no:
			self.serial_no = make_autoname(SERIAL_SERIES)
		self.name = self.serial_no

	def validate(self):
		self._freeze_identity()
		self._sync_status_with_stage()

	def _freeze_identity(self):
		"""Refuse edits to the fields that identify the physical trailer.

		read_only on the field only stops the Desk form; an API write or a
		server-side save would otherwise go straight through.
		"""
		if self.is_new():
			return
		before = self.get_doc_before_save()
		if not before:
			return
		for field in IMMUTABLE_FIELDS:
			old, new = before.get(field), self.get(field)
			if old and old != new:
				frappe.throw(
					_("{0} cannot be changed once it is set. It is embossed on the trailer.").format(
						_(self.meta.get_label(field))
					)
				)

	def _sync_status_with_stage(self):
		"""A trailer still on the line is In Production; nothing else makes sense."""
		if self.status == "In Production" and self.production_stage == "Delivery" and self.warehouse:
			self.status = "In Stock"

	def mark_embossed(self):
		"""Record that the fabrication bay has stamped the plate."""
		if self.chassis_embossed:
			return
		self.db_set(
			{
				"chassis_embossed": 1,
				"embossed_on": frappe.utils.now_datetime(),
				"embossed_by": frappe.session.user,
			},
			update_modified=False,
		)


def get_serials_for_work_order(work_order):
	return frappe.get_all(
		"Trailer Serial",
		filters={"work_order": work_order},
		fields=["name", "serial_no", "chassis_number", "production_stage", "status", "warehouse"],
		order_by="creation asc",
	)
