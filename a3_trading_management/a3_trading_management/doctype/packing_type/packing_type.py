"""Packing Type: how goods are bought on a purchase invoice line (Loose, Box,
Pallet, Packet...), set up in Desk. The portal's New Purchase Invoice lists the
enabled ones. Each one books the line in a UOM; how many pieces are in a pack is
typed on the bill, since a box of screws and a box of bolts hold different counts."""

import frappe
from frappe import _
from frappe.model.document import Document


class PackingType(Document):
	def validate(self):
		self.packing_type = (self.packing_type or "").strip()
		if self.is_loose:
			self.uom = None
			return
		if not self.uom:
			if not frappe.db.exists("UOM", self.packing_type):
				frappe.get_doc({
					"doctype": "UOM", "uom_name": self.packing_type, "must_be_whole_number": 1, "enabled": 1,
				}).insert(ignore_permissions=True)
			self.uom = self.packing_type
		elif not frappe.db.exists("UOM", self.uom):
			frappe.throw(_("Unit of Measure {0} not found").format(self.uom))
