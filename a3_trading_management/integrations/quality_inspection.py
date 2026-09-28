# Implements: Quality Inspection against a Work Order (the Quality screen).
"""Lets a Quality Inspection reference a Work Order.

ERPNext's Quality Inspection only knows receipt, invoice, delivery, stock entry
and job card references. A3's QC happens while the work order is still a draft
at its QC stage — before any job card or Manufacture entry exists — so the
inspection has to point at the work order itself. `setup.install` adds
"Work Order" to the reference_type options; this class keeps the two hooks that
assume a child "<Reference> Item" table carrying a `quality_inspection` column
from running for it (Work Order Item has no such column, so they would fail on
submit and cancel). It also lets the Quality screen record an inspection with no
checks at all, which ERPNext would otherwise refill from the item's checklist.
"""

import frappe
from erpnext.stock.doctype.quality_inspection.quality_inspection import QualityInspection

WORK_ORDER = "Work Order"


class A3QualityInspection(QualityInspection):
	@frappe.whitelist()
	def get_item_specification_details(self):
		# The inspector removed every check on the Quality screen: keep the one
		# pass / fail verdict rather than refilling the item's checklist unread.
		if self.flags.a3_no_checklist:
			return
		return super().get_item_specification_details()

	def set_child_row_reference(self):
		if self.reference_type == WORK_ORDER:
			return
		return super().set_child_row_reference()

	def update_qc_reference(self, remove_reference=False):
		if self.reference_type == WORK_ORDER:
			return
		return super().update_qc_reference(remove_reference=remove_reference)
