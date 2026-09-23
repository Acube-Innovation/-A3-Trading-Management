# Live Stock — read-only data for the Stock Monitoring Board page
# (a3-workshop/live-stock). Everything here only READS ERPNext stock tables
# (Bin, Item Reorder, Material Request, Stock Ledger Entry, Purchase Invoice /
# Receipt). The one action on the page, Request Selected, raises a Purchase
# Material Request through create_material_request below.

import json

import frappe
from frappe import _
from frappe.utils import add_days, cint, flt, fmt_money, formatdate, getdate, nowdate

MOVEMENT_DAYS = 30  # window for the Stock In / Stock Out tables
ROW_LIMIT = 100  # per-table cap so the review page stays light


def _company():
	return (
		frappe.defaults.get_user_default("Company")
		or frappe.db.get_single_value("Global Defaults", "default_company")
		or (frappe.get_all("Company", pluck="name", limit=1) or [None])[0]
	)


def _currency():
	company = _company()
	return (frappe.get_cached_value("Company", company, "default_currency") if company else None) or "INR"


def _qty(v):
	"""5.0 -> '5', 2.50 -> '2.5' — quantities without float noise."""
	v = flt(v)
	return f"{int(v)}" if v == int(v) else f"{round(v, 2):g}"


def _item_meta(item_code):
	return frappe.get_cached_value("Item", item_code, ["item_name", "stock_uom"], as_dict=True) or frappe._dict(
		item_name=item_code, stock_uom=""
	)


def _reorder_rows():
	"""ERPNext Item Reorder child rows: per item + warehouse reorder level/qty."""
	return frappe.get_all(
		"Item Reorder",
		filters={"parenttype": "Item"},
		fields=[
			"parent as item_code",
			"warehouse",
			"warehouse_reorder_level as level",
			"warehouse_reorder_qty as reorder_qty",
		],
	)


def _reorder_map():
	"""{(item_code, warehouse): (level, reorder_qty)} for quick per-bin lookups."""
	return {(r.item_code, r.warehouse): (flt(r.level), flt(r.reorder_qty)) for r in _reorder_rows()}


def _open_mr_by_item():
	"""{item_code: latest open Purchase Material Request} — to flag alerts already
	on a requisition (same rule as workshop._items_on_open_mr, kept local so this
	module never touches the existing purchasing code)."""
	rows = frappe.db.sql(
		"""
		select mri.item_code, mr.name
		from `tabMaterial Request Item` mri
		join `tabMaterial Request` mr on mr.name = mri.parent
		where mr.docstatus < 2 and mr.material_request_type = 'Purchase'
		  and ifnull(mr.status, '') not in ('Cancelled', 'Stopped', 'Received')
		order by mr.creation asc
		""",
		as_dict=True,
	)
	return {r.item_code: r.name for r in rows}  # later (newer) rows win


def _bin_rows():
	"""Every Bin joined with its Item — the live per-warehouse stock position."""
	return frappe.db.sql(
		"""
		select b.item_code, i.item_name, i.stock_uom as uom, b.warehouse,
		       b.actual_qty, b.reserved_qty, b.ordered_qty, b.indented_qty,
		       b.projected_qty, b.valuation_rate, b.stock_value
		from `tabBin` b
		join `tabItem` i on i.name = b.item_code
		where i.disabled = 0
		order by b.warehouse, i.item_name
		""",
		as_dict=True,
	)


def _stock_status(actual, reserved, level):
	"""One traffic-light status per bin row: Out < short-for-reservations < low < ok."""
	if flt(actual) <= 0:
		return "Out of Stock", "badge--danger"
	if flt(actual) < flt(reserved):
		return "Short", "badge--danger"
	if flt(level) > 0 and flt(actual) <= flt(level):
		return "Low Stock", "badge--pending"
	return "In Stock", "badge--success"


def get_current_stock():
	"""Current stock table: one row per item + warehouse (Bin), with reservation,
	incoming (ordered), requisition (indented) and valuation columns."""
	currency = _currency()
	levels = _reorder_map()
	rows = []
	for b in _bin_rows():
		level = levels.get((b.item_code, b.warehouse), (0.0, 0.0))[0]
		status, variant = _stock_status(b.actual_qty, b.reserved_qty, level)
		value = flt(b.stock_value) or flt(b.actual_qty) * flt(b.valuation_rate)
		rows.append(
			{
				"item_code": b.item_code,
				"item_name": b.item_name or b.item_code,
				"uom": b.uom or "",
				"warehouse": b.warehouse,
				"actual": _qty(b.actual_qty),
				"reserved": _qty(b.reserved_qty),
				"ordered": _qty(b.ordered_qty),
				"requested": _qty(b.indented_qty),
				"projected": _qty(b.projected_qty),
				"projected_raw": flt(b.projected_qty),
				"rate": fmt_money(b.valuation_rate, currency=currency),
				"value": fmt_money(value, currency=currency),
				"value_raw": value,
				"reorder_level": _qty(level) if level else "",
				"status": status,
				"variant": variant,
			}
		)
	return rows


def get_warehouse_summary():
	"""Per-warehouse review row: item count, quantities and value, low/out counts.
	Lists every non-group warehouse even when it holds nothing yet."""
	currency = _currency()
	levels = _reorder_map()
	summary = {
		w: {
			"warehouse": w,
			"items": 0,
			"qty": 0.0,
			"reserved": 0.0,
			"ordered": 0.0,
			"requested": 0.0,
			"value": 0.0,
			"low": 0,
			"out": 0,
		}
		for w in frappe.get_all("Warehouse", filters={"is_group": 0, "disabled": 0}, pluck="name", order_by="name")
	}
	for b in _bin_rows():
		s = summary.setdefault(
			b.warehouse,
			{"warehouse": b.warehouse, "items": 0, "qty": 0.0, "reserved": 0.0, "ordered": 0.0,
			 "requested": 0.0, "value": 0.0, "low": 0, "out": 0},
		)
		s["items"] += 1 if flt(b.actual_qty) > 0 else 0
		s["qty"] += flt(b.actual_qty)
		s["reserved"] += flt(b.reserved_qty)
		s["ordered"] += flt(b.ordered_qty)
		s["requested"] += flt(b.indented_qty)
		s["value"] += flt(b.stock_value) or flt(b.actual_qty) * flt(b.valuation_rate)
		level = levels.get((b.item_code, b.warehouse), (0.0, 0.0))[0]
		status = _stock_status(b.actual_qty, b.reserved_qty, level)[0]
		if status in ("Out of Stock", "Short"):
			s["out"] += 1
		elif status == "Low Stock":
			s["low"] += 1
	rows = []
	for s in summary.values():
		rows.append(
			{
				**s,
				"qty": _qty(s["qty"]),
				"reserved": _qty(s["reserved"]),
				"ordered": _qty(s["ordered"]),
				"requested": _qty(s["requested"]),
				"value": fmt_money(s["value"], currency=currency),
				"value_raw": s["value"],
			}
		)
	return rows


def get_low_stock_alerts():
	"""The low-stock alert list: every item at/below its reorder level, plus any bin
	that is out of (or short on) stock while quantities are reserved or requested —
	so shortages surface even before reorder levels are configured."""
	bins = {(b.item_code, b.warehouse): b for b in _bin_rows()}
	on_mr = _open_mr_by_item()
	alerts, seen = [], set()

	def add(item_code, warehouse, available, reserved, level, reorder_qty):
		if (item_code, warehouse) in seen:
			return
		seen.add((item_code, warehouse))
		meta = _item_meta(item_code)
		critical = flt(available) <= 0 or flt(available) < flt(reserved)
		suggested = max(flt(reorder_qty), flt(level) - flt(available), flt(reserved) - flt(available), 1.0)
		alerts.append(
			{
				"item_code": item_code,
				"item_name": meta.item_name or item_code,
				"uom": meta.stock_uom or "",
				"warehouse": warehouse or _("All warehouses"),
				"available": _qty(available),
				"reserved": _qty(reserved),
				"reorder_level": _qty(level) if flt(level) else "—",
				"suggested": _qty(suggested),
				"material_request": on_mr.get(item_code) or "",
				"status": _("Critical") if critical else _("Low"),
				"variant": "badge--danger" if critical else "badge--pending",
				"rank": 0 if critical else 1,
			}
		)

	# 1) Configured reorder levels that have been breached.
	for r in _reorder_rows():
		if flt(r.level) <= 0:
			continue
		b = bins.get((r.item_code, r.warehouse))
		available = flt(b.actual_qty) if b else 0.0
		reserved = flt(b.reserved_qty) if b else 0.0
		if available <= flt(r.level):
			add(r.item_code, r.warehouse, available, reserved, r.level, r.reorder_qty)

	# 2) Bins that are out of / short on stock while something needs them.
	for b in bins.values():
		needed = flt(b.reserved_qty) > 0 or flt(b.indented_qty) > 0 or flt(b.projected_qty) < 0
		if needed and flt(b.actual_qty) < flt(b.reserved_qty):
			add(b.item_code, b.warehouse, b.actual_qty, b.reserved_qty, 0, 0)

	alerts.sort(key=lambda a: (a["rank"], a["item_name"]))
	return alerts


def get_requisitions():
	"""Material Requests (all types), newest first: item/qty totals plus how far
	each is ordered and received — the requisition half of the review board."""
	mrs = frappe.get_all(
		"Material Request",
		filters={"docstatus": ["<", 2]},
		fields=[
			"name", "transaction_date", "material_request_type", "status",
			"per_ordered", "per_received", "docstatus",
		],
		order_by="transaction_date desc, creation desc",
		limit=ROW_LIMIT,
	)
	totals = {
		r.parent: r
		for r in frappe.get_all(
			"Material Request Item",
			filters={"parent": ["in", [m.name for m in mrs] or [""]]},
			# "item_count", not "items" — an alias named items would collide with
			# dict.items on the result row and always read back as 0.
			fields=["parent", "count(name) as item_count", "sum(qty) as qty"],
			group_by="parent",
		)
	}
	variant = {
		"Draft": "badge--soft",
		"Pending": "badge--pending",
		"Partially Ordered": "badge--progress",
		"Partially Received": "badge--progress",
		"Ordered": "badge--navy",
		"Issued": "badge--navy",
		"Transferred": "badge--navy",
		"Received": "badge--success",
		"Manufactured": "badge--success",
		"Stopped": "badge--danger",
	}
	rows = []
	for m in mrs:
		status = "Draft" if m.docstatus == 0 else (m.status or "Pending")
		t = totals.get(m.name) or frappe._dict(item_count=0, qty=0)
		rows.append(
			{
				"name": m.name,
				"date": formatdate(m.transaction_date, "dd MMM yyyy"),
				"type": m.material_request_type,
				"items": cint(t.item_count),
				"qty": _qty(t.qty),
				"per_ordered": cint(m.per_ordered),
				"per_received": cint(m.per_received),
				"status": status,
				"variant": variant.get(status, "badge--soft"),
			}
		)
	return rows


def _movement(direction):
	"""Stock Ledger Entries for the last MOVEMENT_DAYS: +1 inward, -1 outward."""
	currency = _currency()
	op = ">" if direction > 0 else "<"
	rows = frappe.get_all(
		"Stock Ledger Entry",
		filters={
			"is_cancelled": 0,
			"posting_date": [">=", add_days(nowdate(), -MOVEMENT_DAYS)],
			"actual_qty": [op, 0],
		},
		fields=[
			"posting_date", "item_code", "warehouse", "actual_qty",
			"voucher_type", "voucher_no", "stock_value_difference",
		],
		order_by="posting_date desc, posting_time desc, creation desc",
		limit=ROW_LIMIT,
	)
	out = []
	for r in rows:
		meta = _item_meta(r.item_code)
		out.append(
			{
				"date": formatdate(r.posting_date, "dd MMM"),
				"item_code": r.item_code,
				"item_name": meta.item_name or r.item_code,
				"warehouse": r.warehouse,
				"qty": _qty(abs(flt(r.actual_qty))),
				"uom": meta.stock_uom or "",
				"value": fmt_money(abs(flt(r.stock_value_difference)), currency=currency),
				"voucher_type": r.voucher_type,
				"voucher_no": r.voucher_no,
				"voucher_url": "/app/{0}/{1}".format(
					frappe.scrub(r.voucher_type or "").replace("_", "-"), r.voucher_no
				),
			}
		)
	return out


def get_stock_in():
	return _movement(+1)


def get_stock_out():
	return _movement(-1)


def get_payment_missing():
	"""Purchase paperwork with money still open: unpaid/overdue Purchase Invoices,
	plus submitted Purchase Receipts nobody has billed yet."""
	currency = _currency()
	today = getdate(nowdate())
	invoices = []
	for pi in frappe.get_all(
		"Purchase Invoice",
		filters={"docstatus": 1, "outstanding_amount": [">", 0]},
		fields=["name", "supplier_name", "posting_date", "due_date", "grand_total", "outstanding_amount"],
		order_by="due_date asc, posting_date asc",
		limit=ROW_LIMIT,
	):
		overdue = bool(pi.due_date and getdate(pi.due_date) < today)
		invoices.append(
			{
				"name": pi.name,
				"supplier": pi.supplier_name or "",
				"date": formatdate(pi.posting_date, "dd MMM yyyy"),
				"due": formatdate(pi.due_date, "dd MMM yyyy") if pi.due_date else "—",
				"total": fmt_money(pi.grand_total, currency=currency),
				"outstanding": fmt_money(pi.outstanding_amount, currency=currency),
				"outstanding_raw": flt(pi.outstanding_amount),
				"status": _("Overdue") if overdue else _("Unpaid"),
				"variant": "badge--danger" if overdue else "badge--pending",
			}
		)
	to_bill = [
		{
			"name": pr.name,
			"supplier": pr.supplier_name or "",
			"date": formatdate(pr.posting_date, "dd MMM yyyy"),
			"total": fmt_money(pr.grand_total, currency=currency),
		}
		for pr in frappe.get_all(
			"Purchase Receipt",
			filters={"docstatus": 1, "status": "To Bill"},
			fields=["name", "supplier_name", "posting_date", "grand_total"],
			order_by="posting_date asc",
			limit=ROW_LIMIT,
		)
	]
	return {"invoices": invoices, "to_bill": to_bill}


@frappe.whitelist()
def get_stock_board():
	"""Everything the Live Stock page shows, in one call (the page controller
	renders it server-side; it is whitelisted so the page could also refresh live)."""
	if frappe.session.user == "Guest":
		frappe.throw(_("You must be logged in to view the stock board."), frappe.PermissionError)

	currency = _currency()
	stock = get_current_stock()
	warehouses = get_warehouse_summary()
	low_stock = get_low_stock_alerts()
	requisitions = get_requisitions()
	stock_in = get_stock_in()
	stock_out = get_stock_out()
	payments = get_payment_missing()

	open_reqs = [r for r in requisitions if r["status"] not in ("Received", "Manufactured")]
	total_value = sum(r["value_raw"] for r in stock)
	outstanding = sum(i["outstanding_raw"] for i in payments["invoices"])
	summary = {
		"stock_value": fmt_money(total_value, currency=currency),
		"items_in_stock": len({r["item_code"] for r in stock if flt(r["actual"]) > 0}),
		"warehouses": len(warehouses),
		"low_stock": len(low_stock),
		"out_of_stock": len([a for a in low_stock if a["status"] == "Critical"]),
		"open_requisitions": len(open_reqs),
		"payments_due": fmt_money(outstanding, currency=currency),
		"payments_count": len(payments["invoices"]) + len(payments["to_bill"]),
	}
	return {
		"currency": currency,
		"company": _company() or "",
		"movement_days": MOVEMENT_DAYS,
		"summary": summary,
		"warehouses": warehouses,
		"stock": stock,
		"low_stock": low_stock,
		"requisitions": requisitions,
		"stock_in": stock_in,
		"stock_out": stock_out,
		"payments": payments,
	}


# ---------------------------------------------------------------- the one action
def _mr_warehouse():
	"""Where requested material should land: the stock default, else the Stores yard."""
	wh = frappe.db.get_single_value("Stock Settings", "default_warehouse")
	if wh and frappe.db.exists("Warehouse", wh):
		return wh
	return frappe.db.get_value("Warehouse", {"is_group": 0, "warehouse_name": "Stores"}, "name")


def _items_on_open_mr():
	"""Item codes already on an open Purchase Material Request, so the board never
	requests the same item twice."""
	rows = frappe.db.sql(
		"""
		select distinct mri.item_code
		from `tabMaterial Request Item` mri
		join `tabMaterial Request` mr on mr.name = mri.parent
		where mr.docstatus < 2 and mr.material_request_type = 'Purchase'
		  and ifnull(mr.status, '') not in ('Cancelled', 'Stopped', 'Received')
		""",
		as_dict=True,
	)
	return {r.item_code for r in rows}


@frappe.whitelist()
def create_material_request(items=None, force=0):
	"""Raise ONE Purchase Material Request from the low-stock board's ticked rows.

	`items` is a list of {item_code, qty}; when omitted, every current low-stock
	alert is requested. Items already on an open request are skipped unless
	`force`. Standard ERPNext flow: type Purchase, one line per item, UOM and
	conversion filled by the controller."""
	frappe.has_permission("Material Request", "create", throw=True)
	if isinstance(items, str):
		items = json.loads(items)
	if not items:
		items = [{"item_code": a["item_code"], "qty": a.get("need") or a.get("reorder_qty") or 1} for a in get_low_stock_alerts()]

	wanted = {}
	for i in items or []:
		code = (i.get("item_code") or "").strip()
		qty = flt(i.get("qty"))
		if code and qty > 0 and frappe.db.exists("Item", code):
			wanted[code] = max(wanted.get(code, 0.0), qty)
	if not wanted:
		frappe.throw(_("Nothing to request — stock is sufficient."))
	if not cint(force):
		on_mr = _items_on_open_mr()
		wanted = {c: q for c, q in wanted.items() if c not in on_mr}
		if not wanted:
			frappe.throw(_("All of these items are already on an open Material Request."))

	company = _company()
	if not company:
		frappe.throw(_("No Company found. Please set up a Company in ERPNext first."))
	warehouse = _mr_warehouse()
	mr = frappe.new_doc("Material Request")
	mr.material_request_type = "Purchase"
	mr.company = company
	mr.transaction_date = nowdate()
	mr.schedule_date = nowdate()
	for code, qty in wanted.items():
		line = {"item_code": code, "qty": qty, "schedule_date": nowdate()}
		if warehouse:
			line["warehouse"] = warehouse
		mr.append("items", line)
	mr.flags.ignore_mandatory = True
	mr.insert(ignore_permissions=True)
	return {"material_request": mr.name, "items": len(wanted), "company": company}
