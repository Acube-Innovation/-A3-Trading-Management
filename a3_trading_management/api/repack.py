"""Repack: turn stock of one item into another and carry its cost over.

Two uses, both a standard ERPNext Stock Entry of purpose Repack:

* Cut / convert. Material bought by the piece (a 6 m bar, a 100 m roll, a 10 kg
  rod) is kept in Nos; the BOM uses a measured item (the same material per Meter
  or per Kg). Repacking one piece into its measured quantity (6 Meter, or the 9.6
  Kg it actually weighs) gives the measured item the piece's cost spread over
  that quantity, so a BOM line of 2 Meter costs exactly 2 m of the bar.
* From a BOM. ERPNext's own Get Items consumes the BOM's materials for the qty
  and produces the BOM's item, valued at what was consumed.

ERPNext values the produced line from the consumed lines; nothing is priced here.
One produced line per entry: with more than one, ERPNext needs every rate typed."""

import json

import frappe
from frappe import _
from frappe.utils import cint, flt

from a3_trading_management.api._common import default_company

PURPOSE = "Repack"


def _lines(value):
	rows = value if isinstance(value, list) else json.loads(value or "[]")
	return [r for r in rows if (r.get("item_code") or "").strip() and flt(r.get("qty")) > 0]


def _new_entry(from_warehouse, to_warehouse, remarks):
	if not from_warehouse:
		frappe.throw(_("Choose the warehouse the stock is taken from."))
	doc = frappe.new_doc("Stock Entry")
	doc.stock_entry_type = PURPOSE
	doc.purpose = PURPOSE
	doc.company = default_company()
	doc.from_warehouse = from_warehouse
	doc.to_warehouse = to_warehouse or from_warehouse
	doc.remarks = remarks
	return doc


def _summary(doc):
	"""What went in and what came out, with the cost carried across."""
	consumed = [d for d in doc.items if d.s_warehouse and not d.t_warehouse]
	produced = [d for d in doc.items if d.t_warehouse and not d.s_warehouse]
	line = lambda d: {"item_code": d.item_code, "item_name": d.item_name, "qty": flt(d.qty), "uom": d.uom,
	                  # Per the row's own unit (a rod in Kg is priced per Kg, not per Meter).
	                  "warehouse": d.s_warehouse or d.t_warehouse,
	                  "basic_rate": flt(d.basic_rate) * (flt(d.conversion_factor) or 1),
	                  "amount": flt(d.amount)}
	return {
		"name": doc.name if not doc.is_new() else None, "docstatus": doc.docstatus,
		"consumed": [line(d) for d in consumed], "produced": [line(d) for d in produced],
		"consumed_amount": sum(flt(d.amount) for d in consumed),
		"produced_amount": sum(flt(d.amount) for d in produced),
	}


def _finish(doc, submit, preview):
	"""Preview: run ERPNext's validation and costing, report, undo. Otherwise save,
	and post with `submit`."""
	if cint(preview):
		frappe.db.savepoint("repack_preview")
		try:
			doc.insert()
			out = _summary(doc)
			out["name"] = None
			return out
		finally:
			frappe.db.rollback(save_point="repack_preview")
	doc.insert()
	if cint(submit):
		doc.submit()
	return _summary(doc)


@frappe.whitelist()
def create_repack(consume, produce, from_warehouse, to_warehouse=None, remarks=None, submit=0, preview=0):
	"""Cut / convert: consume `consume` ([{item_code, qty, uom}]) from
	`from_warehouse` and produce the one `produce` line ({item_code, qty, uom})
	into `to_warehouse` (the same yard when not given)."""
	consumed = _lines(consume)
	produce = json.loads(produce) if isinstance(produce, str) else produce
	produced = _lines(produce if isinstance(produce, list) else [produce])
	if not consumed:
		frappe.throw(_("Add what is being cut or converted, with a quantity."))
	if len(produced) != 1:
		frappe.throw(_("Give the one item it becomes, with its measured quantity."))
	out = produced[0]
	if any(r["item_code"] == out["item_code"] for r in consumed):
		frappe.throw(_("The item it becomes must be a different item from the one consumed."))

	doc = _new_entry(from_warehouse, to_warehouse, remarks)
	for r in consumed:
		doc.append("items", {"item_code": r["item_code"], "qty": flt(r["qty"]), "uom": r.get("uom") or None,
		                     "s_warehouse": doc.from_warehouse})
	doc.append("items", {"item_code": out["item_code"], "qty": flt(out["qty"]), "uom": out.get("uom") or None,
	                     "t_warehouse": doc.to_warehouse, "is_finished_item": 1})
	return _finish(doc, submit, preview)


@frappe.whitelist()
def repack_from_bom(bom, qty, from_warehouse, to_warehouse=None, remarks=None, submit=0, preview=0):
	"""Consume the BOM's materials for `qty` of its item and produce that item,
	through ERPNext's own Get Items on a Repack entry."""
	if not bom or not frappe.db.exists("BOM", {"name": bom, "docstatus": 1, "is_active": 1}):
		frappe.throw(_("Choose an approved, active BOM."))
	if flt(qty) <= 0:
		frappe.throw(_("Give the quantity to make."))
	doc = _new_entry(from_warehouse, to_warehouse, remarks)
	doc.from_bom = 1
	doc.bom_no = bom
	doc.fg_completed_qty = flt(qty)
	doc.use_multi_level_bom = 0
	doc.get_items()
	return _finish(doc, submit, preview)
