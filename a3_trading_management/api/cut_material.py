"""Cut material: wire, rod, pipe and the like, bought by the piece and used by length.

A cut item is an ordinary batch-tracked item stocked in Meter (or Kg), whose
purchase unit (Nos) converts to the length of one piece: Nos = 100 for a 100 m
roll, Nos = 12 for a 12 m rod, with Kg = 12/70 where a rod's weight is used.
Each physical piece is one Batch, and the batch's qty is what is left of it, so
stock reads as pieces: 100 m x 4, 80 m x 1.

* Receiving: a row of 5 Nos would make one 500 m batch. It is split into five
  rows of 1 Nos, and ERPNext gives each its own batch.
* Consuming: a row with no batch chosen takes the shortest piece that is long
  enough (stock 25, 40, 60 -> the 25). If no piece is, it takes the longest
  pieces first, split across rows.
* Offcuts: a piece too short to use is moved to the Scrap warehouse (kept at
  its value, to be sold) or written off.

Everything here is standard ERPNext stock: no custom fields."""

import frappe
from frappe import _
from frappe.utils import cint, flt

from a3_trading_management.api._common import default_company

SCRAP_WAREHOUSE = "Scrap"


def is_cut_item(item_code):
	item = frappe.get_cached_value("Item", item_code, ["has_batch_no", "has_serial_no", "is_stock_item"], as_dict=True)
	return bool(item and item.has_batch_no and not item.has_serial_no and item.is_stock_item)


def _pieces_uom(uom):
	"""A whole-number unit (Nos) is a count of pieces."""
	return bool(uom and frappe.get_cached_value("UOM", uom, "must_be_whole_number"))


# -- reading pieces --------------------------------------------------------------


def pieces(item_code=None, warehouse=None):
	"""Every piece (batch) with stock left: [{item_code, batch_no, warehouse, qty, value}]."""
	conditions, values = ["sbb.docstatus = 1", "sbb.is_cancelled = 0"], {}
	if item_code:
		conditions.append("sbb.item_code = %(item_code)s")
		values["item_code"] = item_code
	if warehouse:
		conditions.append("sbb.warehouse = %(warehouse)s")
		values["warehouse"] = warehouse
	return frappe.db.sql(
		f"""select sbb.item_code, sbe.batch_no, sbb.warehouse,
				sum(sbe.qty) as qty, sum(sbe.stock_value_difference) as value
			from `tabSerial and Batch Entry` sbe
			join `tabSerial and Batch Bundle` sbb on sbb.name = sbe.parent
			where {' and '.join(conditions)} and ifnull(sbe.batch_no, '') != ''
			group by sbb.item_code, sbe.batch_no, sbb.warehouse
			having sum(sbe.qty) > 0.0001
			order by sbb.item_code, qty""",
		values,
		as_dict=True,
	)


def pick_batches(item_code, warehouse, qty, taken=None):
	"""Best fit for `qty` of `item_code` from `warehouse`: [(batch_no, qty)].

	`taken` ({batch_no: qty}) is what earlier rows of the same entry already
	took, so two rows never count the same metre twice."""
	taken = taken if taken is not None else {}
	available = []
	for p in pieces(item_code, warehouse):
		left = flt(p.qty) - flt(taken.get(p.batch_no))
		if left > 0.0001:
			available.append((p.batch_no, left))
	qty = flt(qty)
	fits = sorted((b for b in available if b[1] >= qty - 0.0001), key=lambda b: b[1])
	if fits:
		return [(fits[0][0], qty)]
	# No single piece is long enough: longest first, so the fewest pieces are cut.
	out = []
	for batch_no, left in sorted(available, key=lambda b: -b[1]):
		if qty <= 0.0001:
			break
		use = min(left, qty)
		out.append((batch_no, use))
		qty -= use
	return out


# -- hooks -----------------------------------------------------------------------


def split_receipt_rows(doc, method=None):
	"""Purchase Receipt / Purchase Invoice (update stock) before_validate: a row of
	n pieces of a cut item becomes n rows of one piece, one batch each."""
	if doc.doctype == "Purchase Invoice" and not cint(doc.get("update_stock")):
		return
	if doc.docstatus != 0 or doc.get("is_return"):
		return
	rows, changed = [], False
	for row in doc.items:
		n = flt(row.qty)
		if (
			n > 1
			and n == int(n)
			and is_cut_item(row.item_code)
			and row.uom != frappe.get_cached_value("Item", row.item_code, "stock_uom")
			and _pieces_uom(row.uom)
			and not row.get("batch_no")
			and not row.get("serial_and_batch_bundle")
			and not flt(row.get("rejected_qty"))
		):
			for _i in range(int(n)):
				piece = frappe.copy_doc(row, ignore_no_copy=False)
				piece.name = None
				# ERPNext fills the stock qty from the UOM conversion on validate.
				piece.qty = piece.received_qty = 1
				rows.append(piece)
			changed = True
		else:
			rows.append(row)
	if changed:
		doc.set("items", [])
		for i, row in enumerate(rows, 1):
			row.idx = i
			doc.append("items", row)


def apply_best_fit(doc, method=None):
	"""Stock Entry before_validate: give every outgoing row of a cut item that has
	no piece chosen the best-fit piece(s), splitting the row when it needs more
	than one."""
	if doc.docstatus != 0:
		return
	taken, rows, changed, notes = {}, [], False, []
	for row in doc.items:
		if row.get("batch_no") or row.get("serial_and_batch_bundle") or not row.s_warehouse:
			_count(taken, row)
			rows.append(row)
			continue
		# Sheets are cut by size (api/sheet_cut.py), never picked by area here.
		if not is_cut_item(row.item_code) or frappe.get_cached_value("Item", row.item_code, "custom_cut_type") == "Sheet":
			rows.append(row)
			continue
		# Before validate a row in Kg may not have its conversion yet.
		if not flt(row.conversion_factor) or (row.uom and row.uom != row.stock_uom and flt(row.conversion_factor) == 1):
			row.conversion_factor = _conversion_factor(row.item_code, row.uom)
		cf = flt(row.conversion_factor) or 1
		need = flt(row.qty) * cf
		picked = pick_batches(row.item_code, row.s_warehouse, need, taken)
		if not picked:
			rows.append(row)
			continue
		for i, (batch_no, stock_qty) in enumerate(picked):
			piece = row if i == 0 else frappe.copy_doc(row, ignore_no_copy=False)
			if i:
				piece.name = None
			piece.batch_no = batch_no
			piece.use_serial_batch_fields = 1
			piece.transfer_qty = stock_qty
			piece.qty = stock_qty / cf
			taken[batch_no] = flt(taken.get(batch_no)) + stock_qty
			rows.append(piece)
		changed = True
		short = need - sum(q for _b, q in picked)
		if len(picked) > 1:
			notes.append(_("{0}: no single piece is long enough, cut from {1} pieces.").format(row.item_code, len(picked)))
		if short > 0.0001:
			notes.append(_("{0}: {1} short of what is needed.").format(row.item_code, flt(short, 3)))
	if changed:
		doc.set("items", [])
		for i, row in enumerate(rows, 1):
			row.idx = i
			doc.append("items", row)
	if notes:
		frappe.msgprint("<br>".join(notes), title=_("Cut material"), indicator="orange")


def _conversion_factor(item_code, uom):
	from erpnext.stock.get_item_details import get_conversion_factor

	stock_uom = frappe.get_cached_value("Item", item_code, "stock_uom")
	if not uom or uom == stock_uom:
		return 1
	return flt(get_conversion_factor(item_code, uom).get("conversion_factor")) or 1


def _count(taken, row):
	if row.get("batch_no") and row.s_warehouse:
		taken[row.batch_no] = flt(taken.get(row.batch_no)) + flt(row.qty) * (flt(row.conversion_factor) or 1)


# -- offcuts ---------------------------------------------------------------------


def scrap_warehouse(company=None):
	company = company or default_company()
	abbr = frappe.get_cached_value("Company", company, "abbr")
	return f"{SCRAP_WAREHOUSE} - {abbr}"


def ensure_scrap_warehouse():
	"""Created once by after_migrate; offcuts are moved here, still at their value."""
	company = default_company()
	if not company or frappe.db.exists("Warehouse", scrap_warehouse(company)):
		return
	parent = frappe.db.get_value("Warehouse", {"company": company, "is_group": 1, "parent_warehouse": ["in", ["", None]]})
	frappe.get_doc({
		"doctype": "Warehouse",
		"warehouse_name": SCRAP_WAREHOUSE,
		"company": company,
		"parent_warehouse": parent,
	}).insert(ignore_permissions=True)


@frappe.whitelist()
def list_pieces(item_code=None, warehouse=None, max_length=None):
	"""Pieces of cut items with what is left of each. With `max_length`, only the
	offcuts at or under it. The Scrap warehouse is left out unless asked for."""
	frappe.has_permission("Stock Entry", "read", throw=True)
	from a3_trading_management.api.sheet_cut import reserved_pieces

	scrap = scrap_warehouse()
	reserved = reserved_pieces()
	out = []
	for p in pieces(item_code, warehouse):
		if not warehouse and p.warehouse == scrap:
			continue
		if max_length not in (None, "") and flt(p.qty) > flt(max_length) + 0.0001:
			continue
		item = frappe.get_cached_value("Item", p.item_code, ["item_name", "stock_uom", "custom_cut_type"], as_dict=True) or {}
		row = {
			"item_code": p.item_code, "item_name": item.get("item_name"), "uom": item.get("stock_uom"),
			"batch_no": p.batch_no, "warehouse": p.warehouse, "qty": flt(p.qty, 3),
			"amount": flt(p.value, 2),
		}
		if item.get("custom_cut_type") == "Sheet":
			from a3_trading_management.api.sheet_cut import piece_info

			info = piece_info(p.item_code, p.batch_no, p.qty)
			row.update({"sheet": True, "length": info.length, "width": info.width, "cutouts": info.cutouts,
			            "is_offcut": info.is_offcut, "reserved_for": reserved.get(p.batch_no)})
		out.append(row)
	return out


@frappe.whitelist()
def scrap_offcut(item_code, batch_no, warehouse, mode="transfer", remarks=None):
	"""Take a whole offcut out of use: `transfer` moves it to the Scrap warehouse at
	its value; `write_off` issues it, expensing the value."""
	frappe.has_permission("Stock Entry", "submit", throw=True)
	from a3_trading_management.api.sheet_cut import reserved_pieces

	if reserved_pieces().get(batch_no):
		frappe.throw(_("Piece {0} is cut for work order {1} and kept for it.").format(batch_no, reserved_pieces()[batch_no]))
	left = next((p.qty for p in pieces(item_code, warehouse) if p.batch_no == batch_no), 0)
	if flt(left) <= 0:
		frappe.throw(_("Piece {0} has nothing left in {1}.").format(batch_no, warehouse))
	purpose = "Material Transfer" if mode == "transfer" else "Material Issue"
	doc = frappe.new_doc("Stock Entry")
	doc.stock_entry_type = doc.purpose = purpose
	doc.company = default_company()
	doc.remarks = remarks or _("Offcut {0} of {1} scrapped").format(batch_no, item_code)
	row = {"item_code": item_code, "qty": flt(left), "s_warehouse": warehouse, "batch_no": batch_no,
	       "use_serial_batch_fields": 1}
	row["uom"] = row["stock_uom"] = frappe.get_cached_value("Item", item_code, "stock_uom")
	row["conversion_factor"] = 1
	if purpose == "Material Transfer":
		row["t_warehouse"] = scrap_warehouse(doc.company)
	doc.append("items", row)
	doc.insert()
	doc.submit()
	return {"name": doc.name, "purpose": purpose, "qty": flt(left)}
