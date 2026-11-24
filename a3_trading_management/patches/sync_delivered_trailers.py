"""24 Nov 2026: trailers on submitted Delivery Notes are Delivered, owned by the
customer and out of our warehouses. Before this, a note for several trailers marked
only the first, and the warehouse stayed "Finished Goods" after delivery."""

import frappe

from a3_trading_management.api.selling import mark_delivered


def execute():
	if not frappe.db.exists("DocType", "Trailer Serial"):
		return
	for name in frappe.get_all("Delivery Note", filters={"docstatus": 1}, pluck="name", order_by="posting_date, creation"):
		mark_delivered(frappe.get_doc("Delivery Note", name))
