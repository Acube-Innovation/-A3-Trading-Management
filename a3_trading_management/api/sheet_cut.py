"""Sheet cutting: plate and sheet bought by the sheet, used by area.

A sheet item (Item.custom_cut_type = "Sheet") is batch-tracked and stocked in
Square Centimeter. Its purchase unit Nos = length x width of one full sheet, so
a receipt of 3 Nos is split into three batches (cut_material.split_receipt_rows),
one batch per physical sheet, at the sheet price spread over its area.

Each piece (batch) carries its outer size and the rectangles already cut out
of it (Batch.custom_cutouts). A blank size means a full sheet of the Item's size.

Cutting a size out of a piece is one Repack Stock Entry: the whole piece goes
out and two pieces come in, both at the piece's rate per cm2 so value is kept:
* the cut piece, l x w;
* ONE leftover with the same outline and the new cut-out added, whatever its
  shape (an L, a U, ...), holding the remaining area.
A circle or another shape is cut and charged as its bounding rectangle.
Sheets are only cut at production (no manual cutting, by the user's decision).

When the work order leaves its first stage (Material -> Fabrication) the user
chooses, per BOM sheet row, where the pieces come from: leftovers only, a new
sheet, or leftovers first and the balance from new. The pieces are cut then and
reserved for that work order; its Manufacture entry at Delivery consumes them."""

import json
import math

import frappe
from frappe import _
from frappe.utils import cint, flt

from a3_trading_management.api._common import default_company
from a3_trading_management.api.cut_material import pieces, scrap_warehouse

EPS = 0.001
SOURCES = ("leftover", "new", "mixed")


# -- items and pieces ----------------------------------------------------------


def is_sheet_item(item_code):
	return bool(item_code) and frappe.get_cached_value("Item", item_code, "custom_cut_type") == "Sheet"


def sheet_size(item_code):
	item = frappe.get_cached_value(
		"Item", item_code, ["custom_sheet_length_cm", "custom_sheet_width_cm"], as_dict=True
	) or {}
	return flt(item.get("custom_sheet_length_cm")), flt(item.get("custom_sheet_width_cm"))


# -- shapes (Cut Shape, set up in Desk) --------------------------------------------


def _dims(value):
	if isinstance(value, dict):
		return value
	try:
		return json.loads(value or "{}") or {}
	except (ValueError, TypeError):
		return {}


def shape_box(shape, dims):
	"""The rectangle (length, width in cm) a shape of these sizes is cut and
	charged as, from the Cut Shape's formulas."""
	from a3_trading_management.a3_trading_management.doctype.cut_shape.cut_shape import evaluate

	if not shape or not frappe.db.exists("Cut Shape", shape):
		frappe.throw(_("Choose a cut shape"))
	s = frappe.get_cached_doc("Cut Shape", shape)
	dims, values = _dims(dims), {}
	for d in s.dimensions:
		v = flt(dims.get(d.key))
		if v <= 0:
			frappe.throw(_("Give the {0} in cm").format(d.label))
		values[d.key] = v
	try:
		l, w = evaluate(s.length_formula, values), evaluate(s.width_formula, values)
	except Exception as e:
		frappe.throw(_("Shape {0}: its formula could not be worked out ({1}). Fix it in Cut Shape.").format(shape, e))
	if l <= 0 or w <= 0:
		frappe.throw(_("Shape {0} gives a cut size of {1} x {2} cm; check its sizes").format(shape, flt(l, 2), flt(w, 2)))
	return flt(l, 3), flt(w, 3)


def legacy_dims(shape, length, width):
	"""Sizes for a BOM row saved before shapes had their own sizes: the first
	size is the cut length, the second the width."""
	keys = [d.key for d in frappe.get_cached_doc("Cut Shape", shape).dimensions] if frappe.db.exists("Cut Shape", shape) else []
	return dict(zip(keys, [flt(length), flt(width)]))


def dims_text(shape, dims):
	"""“Diameter 30, Thickness 2” for the labels on the BOM and production screens."""
	if not shape or not frappe.db.exists("Cut Shape", shape):
		return ""
	dims = _dims(dims)
	return ", ".join(f"{d.label} {flt(dims.get(d.key), 3):g}" for d in frappe.get_cached_doc("Cut Shape", shape).dimensions)


def _cutouts(value):
	if not value:
		return []
	try:
		return [{k: flt(c[k]) for k in ("x", "y", "l", "w")} for c in json.loads(value)]
	except (ValueError, TypeError, KeyError):
		return []


def piece_info(item_code, batch_no, qty=None):
	"""Outline and cut-outs of one piece: {length, width, cutouts, area, is_offcut}."""
	b = frappe.db.get_value(
		"Batch", batch_no,
		["custom_piece_length_cm", "custom_piece_width_cm", "custom_cutouts", "custom_is_offcut", "custom_piece_shape",
		 "custom_parent_piece"],
		as_dict=True,
	) or {}
	length, width = flt(b.get("custom_piece_length_cm")), flt(b.get("custom_piece_width_cm"))
	if not length or not width:
		length, width = sheet_size(item_code)
	cutouts = _cutouts(b.get("custom_cutouts"))
	area = length * width - sum(c["l"] * c["w"] for c in cutouts)
	# Only an uncut sheet as received is "full"; any piece cut from another is a leftover.
	full = not cutouts and not cint(b.get("custom_is_offcut")) and not b.get("custom_parent_piece")
	if qty is not None and flt(qty) < area - 1:
		# Stock says less than the outline holds (issued some other way): a leftover.
		full = False
	return frappe._dict(length=length, width=width, cutouts=cutouts, area=area, is_offcut=not full,
	                    shape=b.get("custom_piece_shape") or "")


def free_spot(length, width, cutouts, l, w):
	"""Where an l x w rectangle (either way round) fits in the outline without
	touching a cut-out: {x, y, l, w}, bottom-left first, or None.

	Any placement that fits can be slid left and down until it rests on the edge
	or on a cut-out, so only those corner positions need trying."""
	xs = sorted({0.0} | {c["x"] + c["l"] for c in cutouts})
	ys = sorted({0.0} | {c["y"] + c["w"] for c in cutouts})
	turns = [(l, w)] if abs(l - w) < EPS else [(l, w), (w, l)]
	for y in ys:
		for x in xs:
			for pl, pw in turns:
				if x + pl > length + EPS or y + pw > width + EPS:
					continue
				if any(x < c["x"] + c["l"] - EPS and c["x"] < x + pl - EPS
				       and y < c["y"] + c["w"] - EPS and c["y"] < y + pw - EPS for c in cutouts):
					continue
				return {"x": flt(x, 3), "y": flt(y, 3), "l": flt(pl, 3), "w": flt(pw, 3)}
	return None


def sheet_pieces(item_code, warehouse=None):
	"""Every piece of a sheet item with stock: full sheets and leftovers, with size."""
	out = []
	for p in pieces(item_code, warehouse):
		info = piece_info(item_code, p.batch_no, p.qty)
		qty = flt(p.qty)
		out.append(frappe._dict(
			item_code=item_code, batch_no=p.batch_no, warehouse=p.warehouse, qty=qty, value=flt(p.value),
			rate=flt(p.value) / qty if qty else 0, length=info.length, width=info.width,
			cutouts=info.cutouts, is_offcut=info.is_offcut, shape=info.shape,
		))
	return out


def fitting_leftovers(item_code, warehouse, l, w, exclude=()):
	"""Leftovers a l x w piece can still be cut from, smallest first. Pieces cut
	for another work order are kept out."""
	out, reserved = [], reserved_pieces()
	for p in sheet_pieces(item_code, warehouse):
		if not p.is_offcut or p.batch_no in exclude or p.batch_no in reserved or p.qty < l * w - EPS:
			continue
		spot = free_spot(p.length, p.width, p.cutouts, l, w)
		if spot:
			p.spot = spot
			out.append(p)
	return sorted(out, key=lambda p: p.qty)


def full_sheets(item_code, warehouse, exclude=()):
	"""Uncut sheets, oldest batch first."""
	return [p for p in sheet_pieces(item_code, warehouse) if not p.is_offcut and p.batch_no not in exclude]


# -- cutting -----------------------------------------------------------------------


def _new_batch(item_code, **fields):
	batch = frappe.get_doc({"doctype": "Batch", "item": item_code, **fields})
	batch.flags.ignore_permissions = True
	batch.insert()
	return batch.name


def cut_piece(item_code, batch_no, warehouse, length, width, shape="Rectangle", remarks=None, submit=True):
	"""Cut `length` x `width` (cm) out of piece `batch_no`: one Repack, the piece out,
	the cut piece and one leftover in. A piece of exactly that size is used as it is.

	Returns {stock_entry, piece, leftover, spot}; `piece` is the batch to consume."""
	if not is_sheet_item(item_code):
		frappe.throw(_("{0} is not a sheet item").format(item_code))
	p = next((x for x in sheet_pieces(item_code, warehouse) if x.batch_no == batch_no), None)
	if not p:
		frappe.throw(_("Piece {0} of {1} has no stock in {2}").format(batch_no, item_code, warehouse))
	l, w = flt(length), flt(width)
	spot = free_spot(p.length, p.width, p.cutouts, l, w)
	if not spot:
		frappe.throw(_("{0} x {1} cm does not fit in piece {2}").format(l, w, batch_no))
	if abs(p.qty - l * w) < 1:
		# Nothing would be left: the piece is the size already.
		return {"stock_entry": None, "piece": batch_no, "leftover": None, "spot": spot, "area": p.qty}

	stock_uom = frappe.get_cached_value("Item", item_code, "stock_uom")
	area = spot["l"] * spot["w"]
	left = flt(p.qty - area, 4)
	cut = _new_batch(item_code, custom_piece_length_cm=spot["l"], custom_piece_width_cm=spot["w"],
	                 custom_piece_shape=shape or "Rectangle", custom_parent_piece=batch_no)
	leftover = _new_batch(
		item_code, custom_piece_length_cm=p.length, custom_piece_width_cm=p.width, custom_is_offcut=1,
		custom_parent_piece=batch_no, custom_cutouts=json.dumps(p.cutouts + [spot]),
	)

	doc = frappe.new_doc("Stock Entry")
	doc.stock_entry_type = doc.purpose = "Repack"
	doc.company = default_company()
	doc.from_warehouse = doc.to_warehouse = warehouse
	doc.remarks = remarks or _("Cut {0} x {1} cm{2} from {3}").format(
		spot["l"], spot["w"], f" ({shape})" if shape and shape != "Rectangle" else "", batch_no)
	common = {"item_code": item_code, "uom": stock_uom, "stock_uom": stock_uom, "conversion_factor": 1,
	          "use_serial_batch_fields": 1}
	doc.append("items", {**common, "qty": p.qty, "s_warehouse": warehouse, "batch_no": batch_no})
	for b, q in ((cut, area), (leftover, left)):
		doc.append("items", {**common, "qty": q, "t_warehouse": warehouse, "batch_no": b, "is_finished_item": 1,
		                     "set_basic_rate_manually": 1, "basic_rate": p.rate})
	doc.flags.ignore_permissions = True
	doc.insert()
	if submit:
		doc.submit()
	return {"stock_entry": doc.name, "piece": cut, "leftover": leftover, "spot": spot, "area": area}


def guard_stock_entry(doc, method=None):
	"""Stock Entry before_validate: a sheet only leaves stock as whole pieces.

	Taking part of a piece without cutting it would leave a batch whose drawing
	no longer matches what is on the floor, so a row of a sheet item must name
	its piece (batch) and take all of it. Rows with a Serial and Batch Bundle
	(entered in Desk by hand) are left to the person who chose them."""
	if doc.docstatus != 0:
		return
	for row in doc.items:
		if not row.s_warehouse or not is_sheet_item(row.item_code) or row.get("serial_and_batch_bundle"):
			continue
		name = frappe.get_cached_value("Item", row.item_code, "item_name") or row.item_code
		how = _("Sheets are cut at production: make it through a work order, which asks whether to use a leftover or a new sheet.")
		if not row.get("batch_no"):
			frappe.throw(_("Row {0}: {1} is a sheet. Choose the piece (batch) to take. {2}").format(row.idx, name, how),
			             title=_("Sheet material"))
		owner = reserved_pieces().get(row.batch_no)
		if owner and owner != doc.get("work_order"):
			frappe.throw(_("Row {0}: piece {1} of {2} is cut for work order {3} and kept for it.")
			             .format(row.idx, row.batch_no, name, owner), title=_("Sheet material"))
		cf = flt(row.conversion_factor) or 1
		have = next((p.qty for p in pieces(row.item_code, row.s_warehouse) if p.batch_no == row.batch_no), 0)
		if abs(flt(row.qty) * cf - flt(have)) > 0.01:
			frappe.throw(_("Row {0}: {1} piece {2} holds {3} cm²; a sheet piece is taken whole, not {4} cm². {5}")
			             .format(row.idx, name, row.batch_no, flt(have, 2), flt(flt(row.qty) * cf, 2), how),
			             title=_("Sheet material"))


# -- production --------------------------------------------------------------------


CUT_FIELDS = ("custom_cut_shape", "custom_cut_length_cm", "custom_cut_width_cm", "custom_cut_pieces", "custom_cut_dims")


def bom_cuts(wo):
	"""The BOM's sheet rows with their cut size, per item: {item_code: [row, ...]}.
	`pieces` is for the whole work order."""
	if not wo.bom_no:
		return {}
	bom_qty = flt(frappe.db.get_value("BOM", wo.bom_no, "quantity")) or 1
	out = {}
	for r in frappe.get_all(
		"BOM Item", filters={"parent": wo.bom_no, "parenttype": "BOM"},
		fields=["name", "item_code", "item_name", "stock_qty", *CUT_FIELDS], order_by="idx",
	):
		if not is_sheet_item(r.item_code) or flt(r.custom_cut_length_cm) <= 0:
			continue
		l, w = flt(r.custom_cut_length_cm), flt(r.custom_cut_width_cm) or flt(r.custom_cut_length_cm)
		per = cint(r.custom_cut_pieces) or max(1, round(flt(r.stock_qty) / (l * w)))
		r.custom_cut_width_cm = w
		r.custom_cut_pieces = int(math.ceil(per * flt(wo.qty) / bom_qty - EPS))
		out.setdefault(r.item_code, []).append(r)
	return out


def wo_row_cuts(wo, bom=None):
	"""The cut size of each sheet row of the Work Order's materials, as
	[(row, cut)] with cut a dict of CUT_FIELDS, or None when it has none.

	A row added or edited on the work order carries its own size. A row that
	came from the BOM and was never saved on the work order takes the BOM's
	(the rows get their own sizes once the materials are saved), as long as no
	row of that item has its own size; each BOM sheet row is used once."""
	bom = {k: list(v) for k, v in (bom if bom is not None else bom_cuts(wo)).items()}
	# Once any row of an item has its own size, the BOM's sizes for it no longer apply.
	for d in wo.required_items:
		if flt(d.get("custom_cut_length_cm")) > 0:
			bom.pop(d.item_code, None)
	out = []
	for d in wo.required_items:
		if not is_sheet_item(d.item_code):
			continue
		if flt(d.get("custom_cut_length_cm")) > 0:
			out.append((d, {f: d.get(f) for f in CUT_FIELDS}))
		elif bom.get(d.item_code):
			# ERPNext merges BOM rows of one item into one work order row.
			for b in bom.pop(d.item_code):
				out.append((d, {f: b.get(f) for f in CUT_FIELDS}))
		else:
			out.append((d, None))
	return out


def needs(wo):
	"""The sheet rows of a Work Order's materials: what has to be cut, how many pieces."""
	out = []
	for k, (d, c) in enumerate(wo_row_cuts(wo)):
		if not c:
			frappe.throw(_("Work order {0}: sheet {1} (row {2}) has no cut size. Set it with the scissors on the material row.")
			             .format(wo.name, d.item_name or d.item_code, d.idx), title=_("Sheet material"))
		l = flt(c["custom_cut_length_cm"])
		w = flt(c["custom_cut_width_cm"]) or l
		pieces = cint(c["custom_cut_pieces"]) or max(1, int(math.ceil(flt(d.required_qty) / (l * w) - EPS)))
		dims = _dims(c["custom_cut_dims"]) or legacy_dims(c["custom_cut_shape"], l, w)
		out.append(frappe._dict(
			# Row key: stays the same while the materials are re-saved unchanged.
			row="{0}:{1}:{2}".format(k, d.item_code, flt(l * w * pieces, 2)),
			item_code=d.item_code, item_name=d.item_name, shape=c["custom_cut_shape"] or "Rectangle",
			dims_text=dims_text(c["custom_cut_shape"], dims),
			length=l, width=w, pieces=pieces,
			warehouse=d.source_warehouse or wo.source_warehouse or wo.wip_warehouse,
		))
	return out


def plan_doc(value):
	"""Work Order.custom_sheet_plan: {"choices": [{row, source, leftovers}],
	"cut": [[item_code, batch_no, area, warehouse], ...]}. "cut" is filled when
	the pieces are cut at the first stage. (An older plan is a bare choices list.)"""
	v = value if isinstance(value, (list, dict)) else json.loads(value or "null")
	if isinstance(v, list):
		return {"choices": v, "cut": []}
	return {"choices": (v or {}).get("choices") or [], "cut": (v or {}).get("cut") or []}


def _plan(value):
	rows = value if isinstance(value, list) else plan_doc(value)["choices"]
	return {r.get("row"): r for r in rows if r.get("row")}


def reserved_pieces(exclude_wo=None):
	"""{batch_no: work order} for pieces cut for a work order not yet delivered."""
	out = {}
	for wo in frappe.get_all("Work Order", filters={"custom_sheet_plan": ["like", '%"cut": [[%'],
	                                                  "docstatus": ["!=", 2], "custom_production_stage": ["!=", "Delivery"]},
	                         fields=["name", "custom_sheet_plan"]):
		if wo.name == exclude_wo:
			continue
		for c in plan_doc(wo.custom_sheet_plan)["cut"]:
			out[c[1]] = wo.name
	return out


def check_plan(wo, plan):
	"""Every sheet row has a choice, and leftovers-only rows have enough leftovers.
	Returns the plan as stored, or throws."""
	rows = needs(wo)
	if not rows:
		return None
	plan = _plan(plan)
	missing = [r.item_name or r.item_code for r in rows if r.row not in plan]
	if missing:
		frappe.throw(
			_("Choose where the sheet material comes from (leftover or new sheet) for: {0}").format(", ".join(missing)),
			title=_("Sheet material"),
		)
	for r in rows:
		c = plan[r.row]
		if c.get("source") not in SOURCES:
			frappe.throw(_("{0}: choose leftover, new sheet or both").format(r.item_name))
		if c.get("source") == "leftover" and not c.get("leftovers"):
			frappe.throw(_("{0}: tick the leftover(s) to cut from").format(r.item_name))
	return [{"row": r.row, "source": plan[r.row]["source"],
	         "leftovers": list(plan[r.row].get("leftovers") or [])} for r in rows]


def cut_for_work_order(wo, plan):
	"""Cut every sheet piece the Work Order needs as the plan says. Returns
	[(item_code, batch_no, area, warehouse)] for the Manufacture entry."""
	plan = _plan(plan)
	used = []
	for r in needs(wo):
		c = plan.get(r.row) or {}
		source = c.get("source")
		queue = list(c.get("leftovers") or []) if source in ("leftover", "mixed") else []
		current = None
		for k in range(r.pieces):
			got = None
			while not got and (current or queue):
				cand, current = (current, None) if current else (queue.pop(0), None)
				p = next((x for x in sheet_pieces(r.item_code, r.warehouse) if x.batch_no == cand), None)
				if p and free_spot(p.length, p.width, p.cutouts, r.length, r.width):
					res = cut_piece(r.item_code, cand, r.warehouse, r.length, r.width, r.shape,
					                remarks=_("{0}: piece {1} of {2}").format(wo.name, k + 1, r.pieces))
					got, current = res, res["leftover"]
			if not got:
				if source == "leftover":
					frappe.throw(_("{0}: the leftovers chosen hold only {1} of the {2} pieces of {3} x {4} cm. "
					               "Choose leftover + new sheet.").format(r.item_name, k, r.pieces, r.length, r.width))
				sheets = full_sheets(r.item_code, r.warehouse)
				if not sheets:
					frappe.throw(_("{0}: no full sheet left in {1} for piece {2} of {3}")
					             .format(r.item_name, r.warehouse, k + 1, r.pieces))
				res = cut_piece(r.item_code, sheets[0].batch_no, r.warehouse, r.length, r.width, r.shape,
				                remarks=_("{0}: piece {1} of {2}").format(wo.name, k + 1, r.pieces))
				got, current = res, res["leftover"]
			used.append((r.item_code, got["piece"], got["area"], r.warehouse))
	return used


def apply_to_manufacture_entry(se, used):
	"""Swap the Manufacture entry's sheet rows for one row per cut piece."""
	sheet_items = {u[0] for u in used}
	template = {}
	rows = []
	for d in se.items:
		if d.s_warehouse and d.item_code in sheet_items:
			template.setdefault(d.item_code, d)
			continue
		rows.append(d)
	for item_code, batch_no, area, warehouse in used:
		t = template.get(item_code)
		stock_uom = frappe.get_cached_value("Item", item_code, "stock_uom")
		row = frappe.copy_doc(t, ignore_no_copy=False) if t else frappe._dict(item_code=item_code)
		if t:
			row.name = None
		row.update({"qty": area, "transfer_qty": area, "uom": stock_uom, "stock_uom": stock_uom,
		            "conversion_factor": 1, "s_warehouse": (t and t.s_warehouse) or warehouse, "t_warehouse": None,
		            "batch_no": batch_no, "use_serial_batch_fields": 1, "serial_and_batch_bundle": None})
		rows.append(row)
	se.set("items", [])
	for i, row in enumerate(rows, 1):
		row.idx = i
		se.append("items", row)


# -- portal APIs ---------------------------------------------------------------------


def _piece_out(p):
	return {"batch_no": p.batch_no, "warehouse": p.warehouse, "qty": flt(p.qty, 2), "value": flt(p.value, 2),
	        "length": p.length, "width": p.width, "cutouts": p.cutouts, "is_offcut": p.is_offcut,
	        "shape": p.shape}


@frappe.whitelist()
def get_cut_shapes():
	"""The enabled shapes for the Cut size window, each with the sizes it asks for."""
	out = []
	for name in frappe.get_all("Cut Shape", filters={"enabled": 1}, order_by="sort_order asc, name asc", pluck="name"):
		s = frappe.get_cached_doc("Cut Shape", name)
		out.append({"name": s.name, "note": s.note or "",
		            "dims": [{"key": d.key, "label": d.label, "hint": d.hint or ""} for d in s.dimensions]})
	return out


@frappe.whitelist()
def get_sheet_info(item, warehouse=None, shape=None, dims=None):
	"""Sheet size and stock; with a shape and its sizes, also the rectangle it is
	cut as and how many leftovers that fits in."""
	frappe.has_permission("Item", "read", throw=True)
	if not is_sheet_item(item):
		return {"is_sheet": False}
	sl, sw = sheet_size(item)
	thickness = frappe.get_cached_value("Item", item, "custom_sheet_thickness_mm")
	reserved = reserved_pieces()
	all_pieces = [p for p in sheet_pieces(item, warehouse) if p.warehouse != scrap_warehouse() and p.batch_no not in reserved]
	out = {"is_sheet": True, "length": sl, "width": sw, "thickness": flt(thickness),
	       "stock_uom": frappe.get_cached_value("Item", item, "stock_uom"),
	       "full_sheets": sum(1 for p in all_pieces if not p.is_offcut),
	       "leftovers": sum(1 for p in all_pieces if p.is_offcut)}
	if shape and _dims(dims):
		l, w = shape_box(shape, dims)
		out["cut_length"], out["cut_width"] = l, w
		out["fits_sheet"] = bool(free_spot(sl, sw, [], l, w))
		out["fitting_leftovers"] = sum(
			1 for p in all_pieces if p.is_offcut and p.qty >= l * w - EPS and free_spot(p.length, p.width, p.cutouts, l, w)
		)
	return out


@frappe.whitelist()
def sheet_needs(work_order):
	"""For the production dialog: each sheet row with its size, pieces, the
	leftovers it fits in and the full sheets in stock."""
	wo = frappe.get_doc("Work Order", work_order)
	wo.check_permission("read")
	pd = plan_doc(wo.get("custom_sheet_plan"))
	if pd["cut"]:
		return []   # already cut at the first stage
	saved = _plan(pd["choices"])
	out = []
	for r in needs(wo):
		out.append({
			**r, "choice": saved.get(r.row),
			"leftovers": [_piece_out(p) for p in fitting_leftovers(r.item_code, r.warehouse, r.length, r.width)],
			"full_sheets": len(full_sheets(r.item_code, r.warehouse)),
			"sheet_length": sheet_size(r.item_code)[0], "sheet_width": sheet_size(r.item_code)[1],
		})
	return out


def cut_summary(wo):
	"""The pieces cut for a work order, for its detail screen."""
	out = []
	for item_code, batch_no, area, warehouse in plan_doc(wo.get("custom_sheet_plan"))["cut"]:
		b = frappe.db.get_value("Batch", batch_no, ["custom_piece_length_cm", "custom_piece_width_cm", "custom_piece_shape"], as_dict=True) or {}
		out.append({"item_code": item_code, "item_name": frappe.get_cached_value("Item", item_code, "item_name"),
		            "batch_no": batch_no, "area": flt(area, 2), "warehouse": warehouse,
		            "length": flt(b.get("custom_piece_length_cm")), "width": flt(b.get("custom_piece_width_cm")),
		            "shape": b.get("custom_piece_shape") or ""})
	return out
