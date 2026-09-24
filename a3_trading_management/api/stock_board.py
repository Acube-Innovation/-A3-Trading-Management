# Implements: Live Stock — total quantity existing for each item together with the
# warehouse and location holding it, trailers as identified units (task 29).
"""Additions to the stock board for A3 Trading (EXTRA POINT 4).

The existing board shows one row per item and warehouse. Two things the trading
business needs on top:

* the TOTAL quantity existing for each item, with every warehouse holding it named
  alongside — so a buyer can see "40 in all, across three stores" without adding up
  the rows themselves;
* serialised trailers listed as the identified units they are. A trailer is not a
  quantity: it has a chassis number, and stock of trailers is a list of those.
"""

import frappe
from frappe import _
from frappe.utils import flt


@frappe.whitelist()
def item_totals(limit=500):
	"""Total quantity per item, with the warehouses and locations holding it."""
	frappe.has_permission("Bin", "read", throw=True)
	bins = frappe.get_all(
		"Bin",
		fields=["item_code", "warehouse", "actual_qty", "stock_uom"],
		filters={"actual_qty": ["!=", 0]},
		limit_page_length=0,
	)
	if not bins:
		return []

	names = {b.item_code for b in bins}
	item_names = dict(frappe.get_all(
		"Item", filters={"name": ["in", list(names)]}, fields=["name", "item_name"], as_list=True
	)) if names else {}

	out = {}
	for b in bins:
		row = out.setdefault(b.item_code, {
			"item_code": b.item_code,
			"item_name": item_names.get(b.item_code) or b.item_code,
			"uom": b.stock_uom or "",
			"total_qty": 0.0,
			"locations": [],
		})
		row["total_qty"] += flt(b.actual_qty)
		row["locations"].append({"warehouse": b.warehouse, "qty": flt(b.actual_qty)})

	rows = sorted(out.values(), key=lambda r: -r["total_qty"])
	for r in rows:
		r["locations"].sort(key=lambda l: -l["qty"])
		r["warehouse_count"] = len(r["locations"])
		# A short "where it is" line for the board, longest holding first.
		r["where"] = ", ".join(
			f"{l['warehouse']} ({flt(l['qty']):g})" for l in r["locations"][:3]
		) + (f" +{len(r['locations']) - 3} more" if len(r["locations"]) > 3 else "")
	return rows[: int(limit or 500)]


@frappe.whitelist()
def trailer_units():
	"""Trailers in stock as identified units, not a count.

	Grouped by warehouse so the board can show where each chassis is standing.
	"""
	if not frappe.db.exists("DocType", "Trailer Serial"):
		return []
	frappe.has_permission("Trailer Serial", "read", throw=True)
	rows = frappe.get_all(
		"Trailer Serial",
		filters={"status": "In Stock"},
		fields=["name", "serial_no", "chassis_number", "trailer_type", "warehouse",
		        "production_stage", "chassis_embossed"],
		order_by="warehouse asc, creation asc",
	)
	grouped = {}
	for r in rows:
		grouped.setdefault(r.warehouse or _("Unassigned"), []).append(r)
	return [
		{"warehouse": wh, "count": len(units), "units": units}
		for wh, units in sorted(grouped.items())
	]


@frappe.whitelist()
def stock_additions():
	"""Both additions in one call, for the Live Stock page controller."""
	totals = item_totals()
	trailers = trailer_units()
	return {
		"item_totals": totals,
		"trailer_groups": trailers,
		"trailer_count": sum(g["count"] for g in trailers),
		"distinct_items": len(totals),
	}
