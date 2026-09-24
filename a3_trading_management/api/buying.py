# Implements: Phase 3 — suppliers, compliance blocking, requisitions, purchase
# orders, goods receipt, purchase invoices and supplier payments (tasks 13-19).
"""The buying side of the trading portal.

Each screen reads the ERPNext document that already carries the data — Material
Request, Purchase Order, Purchase Receipt, Purchase Invoice, Payment Entry — and
each action calls that document's own submit. No balance is recomputed here.
"""

import frappe
from frappe import _
from frappe.utils import flt, getdate, nowdate, date_diff

from a3_trading_management.api._common import (
	rows, status_counts, submit_document, cancel_document, default_company, ageing_bucket,
)

# The three papers a supplier must keep current, and the field holding each expiry.
COMPLIANCE_DOCS = {
	"Trade Licence": "custom_compliance_trade_license_expiry",
	"VAT Certificate": "custom_compliance_vat_cert_expiry",
	"Establishment Card": "custom_compliance_establishment_expiry",
}

BLOCKING_STATUSES = {"Not Onboarded", "Documents Pending", "Suspended", "Blocked"}


# ---------------------------------------------------------------------------
# Task 13 — Suppliers screen
# ---------------------------------------------------------------------------

@frappe.whitelist()
def list_suppliers(search=None, status=None, limit=300):
	fields = [
		"name", "supplier_name", "supplier_group", "country", "disabled",
		"custom_compliance_status", "custom_compliance_score",
		"custom_compliance_trade_license_expiry", "custom_compliance_vat_cert_expiry",
		"custom_compliance_establishment_expiry", "custom_compliance_blocks_po",
	]
	# Only the compliance fields are optional. `name` and the stock Supplier fields
	# must never be filtered out — `name` is the primary key, not a DocField, so a
	# blanket meta check would silently drop it and leave the screen with no codes.
	fields = [f for f in fields if not f.startswith("custom_") or _has_field("Supplier", f)]
	filters = {}
	if status:
		filters["custom_compliance_status"] = status
	out = rows("Supplier", fields, filters=filters, search=search,
	           search_fields=["name", "supplier_name"], limit=limit, order_by="supplier_name asc")
	for s in out:
		s["compliance"] = compliance_state(s)
	return out


def compliance_state(supplier_row):
	"""Which papers have lapsed, and whether that stops a purchase order.

	Read off the expiry dates and the status on the supplier profile — the same
	two things the block below tests, so the screen and the block can never
	disagree about a supplier.
	"""
	today = getdate(nowdate())
	expired, expiring = [], []
	for label, field in COMPLIANCE_DOCS.items():
		value = supplier_row.get(field)
		if not value:
			continue
		days = date_diff(getdate(value), today)
		if days < 0:
			expired.append(label)
		elif days <= 30:
			expiring.append(f"{label} ({days}d)")

	status = supplier_row.get("custom_compliance_status")
	blocked = bool(expired) or status in BLOCKING_STATUSES or bool(
		supplier_row.get("custom_compliance_blocks_po")
	)
	return {
		"expired": expired,
		"expiring_soon": expiring,
		"blocked": blocked,
		"state": "Blocked" if blocked else ("Expiring" if expiring else "Clear"),
	}


@frappe.whitelist()
def get_supplier(name):
	frappe.has_permission("Supplier", "read", throw=True)
	doc = frappe.get_doc("Supplier", name).as_dict()
	doc["compliance"] = compliance_state(doc)
	doc["open_orders"] = frappe.db.count("Purchase Order", {"supplier": name, "docstatus": 0})
	doc["outstanding"] = flt(
		frappe.db.get_value(
			"Purchase Invoice",
			{"supplier": name, "docstatus": 1},
			"sum(outstanding_amount)",
		)
	)
	return doc


# ---------------------------------------------------------------------------
# Task 14 — compliance blocking
# ---------------------------------------------------------------------------

def block_non_compliant_supplier(doc, method=None):
	"""Stop a purchase order going to a supplier whose papers have lapsed.

	Hooked on Purchase Order validate so it holds for the portal, the Desk and
	any import alike. Driven off the expiry dates and compliance status held on
	the supplier profile — nothing is duplicated onto the order.
	"""
	if not doc.get("supplier"):
		return
	fields = ["custom_compliance_status", "custom_compliance_blocks_po"] + list(COMPLIANCE_DOCS.values())
	# Only the compliance fields are optional. `name` and the stock Supplier fields
	# must never be filtered out — `name` is the primary key, not a DocField, so a
	# blanket meta check would silently drop it and leave the screen with no codes.
	fields = [f for f in fields if not f.startswith("custom_") or _has_field("Supplier", f)]
	if not fields:
		return  # a site without the compliance fields has nothing to enforce

	values = frappe.db.get_value("Supplier", doc.supplier, fields, as_dict=True) or {}
	state = compliance_state(values)
	if not state["blocked"]:
		return

	reasons = []
	if state["expired"]:
		reasons.append(_("lapsed: {0}").format(", ".join(state["expired"])))
	if values.get("custom_compliance_status") in BLOCKING_STATUSES:
		reasons.append(_("compliance status is {0}").format(values["custom_compliance_status"]))
	if values.get("custom_compliance_blocks_po"):
		reasons.append(_("the supplier is flagged as blocked"))

	frappe.throw(
		_("Purchase Order cannot be raised for {0}: {1}. Update the supplier's compliance "
		  "documents before ordering.").format(doc.supplier, "; ".join(reasons)),
		title=_("Supplier not compliant"),
	)


# ---------------------------------------------------------------------------
# Task 15 — Purchase Requisitions (Material Request, type Purchase)
# ---------------------------------------------------------------------------

@frappe.whitelist()
def list_requisitions(search=None, status=None, limit=200):
	filters = {"material_request_type": "Purchase"}
	if status:
		filters["status"] = status
	out = rows(
		"Material Request",
		["name", "transaction_date", "schedule_date", "status", "docstatus",
		 "company", "per_ordered", "title"],
		filters=filters, search=search, search_fields=["name", "title"], limit=limit,
	)
	orders = {}
	for row in frappe.get_all("Purchase Order Item", filters={"material_request": ["in", [r.name for r in out] or [""]], "docstatus": ["<", 2]},
	                          fields=["parent", "material_request"]):
		orders.setdefault(row.material_request, set()).add(row.parent)
	for r in out:
		r["items"] = frappe.get_all(
			"Material Request Item", filters={"parent": r["name"]},
			fields=["item_code", "item_name", "qty", "stock_uom", "warehouse", "ordered_qty"],
		)
		r["purchase_orders"] = sorted(orders.get(r.name, ()))
	return out


@frappe.whitelist()
def create_requisition(items, schedule_date=None, company=None):
	"""Raise a requisition — from a low-stock alert, a work order or the counter."""
	frappe.has_permission("Material Request", "create", throw=True)
	if isinstance(items, str):
		items = frappe.parse_json(items)
	if not items:
		frappe.throw(_("Add at least one item to the requisition"))

	mr = frappe.new_doc("Material Request")
	mr.material_request_type = "Purchase"
	mr.company = company or default_company()
	mr.transaction_date = nowdate()
	mr.schedule_date = schedule_date or frappe.utils.add_days(nowdate(), 7)
	for row in items:
		mr.append("items", {
			"item_code": row.get("item_code"),
			"qty": flt(row.get("qty")) or 1,
			"warehouse": row.get("warehouse"),
			"schedule_date": mr.schedule_date,
		})
	mr.insert(ignore_permissions=True)
	frappe.db.commit()
	return {"name": mr.name}


@frappe.whitelist()
def approve_requisition(name):
	return submit_document("Material Request", name)


@frappe.whitelist()
def reject_requisition(name, reason=None):
	"""Reject a draft requisition. ERPNext has no Rejected state for a draft, so
	the request is cancelled after submission, or deleted while still a draft."""
	frappe.has_permission("Material Request", "write", throw=True)
	doc = frappe.get_doc("Material Request", name)
	if doc.docstatus == 0:
		doc.add_comment("Comment", _("Rejected: {0}").format(reason or _("no reason given")))
		doc.delete(ignore_permissions=True)
		frappe.db.commit()
		return {"name": name, "result": "deleted"}
	doc.cancel()
	frappe.db.commit()
	return {"name": name, "result": "cancelled"}


@frappe.whitelist()
def requisition_to_order(name, supplier):
	"""Convert an approved requisition into a draft Purchase Order."""
	frappe.has_permission("Purchase Order", "create", throw=True)
	from erpnext.stock.doctype.material_request.material_request import make_purchase_order

	po = make_purchase_order(name)
	po.supplier = supplier
	po.flags.ignore_permissions = True
	po.insert(ignore_permissions=True)   # compliance block runs here
	frappe.db.commit()
	return {"name": po.name}


# ---------------------------------------------------------------------------
# Task 16 — Purchase Orders
# ---------------------------------------------------------------------------

@frappe.whitelist()
def list_purchase_orders(search=None, status=None, supplier=None, limit=200, material_request=None):
	filters = {}
	if status:
		filters["status"] = status
	if supplier:
		filters["supplier"] = supplier
	out = rows(
		"Purchase Order",
		["name", "supplier", "supplier_name", "transaction_date", "schedule_date",
		 "status", "docstatus", "grand_total", "currency", "per_received", "per_billed"],
		filters=filters, search=search, search_fields=["name", "supplier", "supplier_name"],
		limit=limit,
	)
	# Each order line remembers the requisition it was made from; the order shows
	# them so the chain requisition -> order -> receipt can be followed either way.
	links = {}
	for r in frappe.get_all("Purchase Order Item", filters={"parent": ["in", [o.name for o in out] or [""]], "material_request": ["is", "set"]},
	                        fields=["parent", "material_request"]):
		links.setdefault(r.parent, set()).add(r.material_request)
	for o in out:
		o["material_requests"] = sorted(links.get(o.name, ()))
	if material_request:
		out = [o for o in out if material_request in o["material_requests"]]
	return out


@frappe.whitelist()
def submit_purchase_order(name):
	return submit_document("Purchase Order", name)


@frappe.whitelist()
def cancel_purchase_order(name):
	return cancel_document("Purchase Order", name)


# ---------------------------------------------------------------------------
# Task 17 — Goods Receipt (Purchase Receipt), with serial capture
# ---------------------------------------------------------------------------

@frappe.whitelist()
def list_goods_receipts(search=None, limit=200, material_request=None, purchase_order=None):
	out = rows(
		"Purchase Receipt",
		["name", "supplier", "supplier_name", "posting_date", "status", "docstatus",
		 "grand_total", "currency"],
		search=search, search_fields=["name", "supplier", "supplier_name"], limit=limit,
	)
	for r in out:
		r["items"] = frappe.get_all(
			"Purchase Receipt Item", filters={"parent": r["name"]},
			fields=["item_code", "item_name", "qty", "rejected_qty", "warehouse", "serial_no", "purchase_order", "material_request"],
		)
		r["purchase_orders"] = sorted({i.purchase_order for i in r["items"] if i.purchase_order})
		r["material_requests"] = sorted({i.material_request for i in r["items"] if i.material_request})
	if material_request:
		out = [r for r in out if material_request in r["material_requests"]]
	if purchase_order:
		out = [r for r in out if purchase_order in r["purchase_orders"]]
	return out


@frappe.whitelist()
def receive_against_order(purchase_order, rows_json=None):
	"""Receive stock against an order, carrying accepted/rejected quantities and
	serial numbers where the item is serialised."""
	frappe.has_permission("Purchase Receipt", "create", throw=True)
	from erpnext.buying.doctype.purchase_order.purchase_order import make_purchase_receipt

	pr = make_purchase_receipt(purchase_order)
	supplied = frappe.parse_json(rows_json) if isinstance(rows_json, str) else (rows_json or {})
	if supplied:
		by_item = {r.get("item_code"): r for r in supplied if r.get("item_code")}
		for item in pr.items:
			given = by_item.get(item.item_code)
			if not given:
				continue
			if given.get("qty") is not None:
				item.qty = flt(given["qty"])
			if given.get("rejected_qty") is not None:
				item.rejected_qty = flt(given["rejected_qty"])
			if given.get("serial_no"):
				item.serial_no = given["serial_no"]
	pr.flags.ignore_permissions = True
	pr.insert(ignore_permissions=True)
	frappe.db.commit()
	return {"name": pr.name}


@frappe.whitelist()
def submit_goods_receipt(name):
	return submit_document("Purchase Receipt", name)


# ---------------------------------------------------------------------------
# Task 18 — Purchase Invoices, with a hold/approval step before the ledger
# ---------------------------------------------------------------------------

@frappe.whitelist()
def list_purchase_invoices(search=None, status=None, limit=200):
	filters = {}
	if status:
		filters["status"] = status
	return rows(
		"Purchase Invoice",
		["name", "supplier", "supplier_name", "posting_date", "due_date", "status",
		 "docstatus", "grand_total", "outstanding_amount", "currency", "custom_approval_hold"],
		filters=filters, search=search, search_fields=["name", "supplier", "supplier_name"],
		limit=limit,
	)


@frappe.whitelist()
def bill_against(purchase_receipt=None, purchase_order=None):
	"""Raise a draft bill against a receipt or an order."""
	frappe.has_permission("Purchase Invoice", "create", throw=True)
	if purchase_receipt:
		from erpnext.stock.doctype.purchase_receipt.purchase_receipt import make_purchase_invoice
		pi = make_purchase_invoice(purchase_receipt)
	elif purchase_order:
		from erpnext.buying.doctype.purchase_order.purchase_order import make_purchase_invoice
		pi = make_purchase_invoice(purchase_order)
	else:
		frappe.throw(_("Pass a purchase receipt or a purchase order to bill against"))
	pi.flags.ignore_permissions = True
	pi.insert(ignore_permissions=True)
	frappe.db.commit()
	return {"name": pi.name}


@frappe.whitelist()
def set_invoice_hold(name, hold=1, reason=None):
	"""The hold step: a bill on hold cannot be approved into the ledger.

	Deliberately NOT ERPNext's `on_hold`, which is a *payment* hold and can only
	be set after submitting — by then the invoice has already posted, which is the
	opposite of what this gate is for.
	"""
	frappe.has_permission("Purchase Invoice", "write", throw=True)
	if frappe.db.get_value("Purchase Invoice", name, "docstatus") != 0:
		frappe.throw(_("{0} has already been posted; the approval hold only applies to a draft.").format(name))
	frappe.db.set_value("Purchase Invoice", name, {
		"custom_approval_hold": int(hold),
		"custom_hold_reason": reason or "",
	}, update_modified=False)
	frappe.db.commit()
	return {"name": name, "on_hold": int(hold)}


@frappe.whitelist()
def approve_purchase_invoice(name):
	"""Approval is what posts the bill — refused while it is on hold."""
	if frappe.db.get_value("Purchase Invoice", name, "custom_approval_hold"):
		frappe.throw(_("{0} is on hold. Release the hold before approving it.").format(name))
	return submit_document("Purchase Invoice", name)


# ---------------------------------------------------------------------------
# Task 19 — Supplier Payments
# ---------------------------------------------------------------------------

@frappe.whitelist()
def list_supplier_payments(search=None, supplier=None, limit=200):
	filters = {"payment_type": "Pay", "party_type": "Supplier"}
	if supplier:
		filters["party"] = supplier
	return rows(
		"Payment Entry",
		["name", "party", "party_name", "posting_date", "paid_amount",
		 "unallocated_amount", "status", "docstatus", "mode_of_payment", "paid_from"],
		filters=filters, search=search, search_fields=["name", "party", "party_name"], limit=limit,
	)


@frappe.whitelist()
def supplier_outstanding():
	"""What is outstanding by supplier and by age."""
	frappe.has_permission("Purchase Invoice", "read", throw=True)
	today = getdate(nowdate())
	out = {}
	for inv in frappe.get_all(
		"Purchase Invoice",
		filters={"docstatus": 1, "outstanding_amount": [">", 0]},
		fields=["supplier", "supplier_name", "name", "due_date", "outstanding_amount", "currency"],
	):
		entry = out.setdefault(inv.supplier, {
			"supplier": inv.supplier, "supplier_name": inv.supplier_name,
			"currency": inv.currency, "total": 0,
			"buckets": {"Not due": 0, "1-30": 0, "31-60": 0, "61-90": 0, "90+": 0},
			"invoices": [],
		})
		days = date_diff(today, getdate(inv.due_date)) if inv.due_date else 0
		bucket = ageing_bucket(days)
		entry["total"] += flt(inv.outstanding_amount)
		entry["buckets"][bucket] += flt(inv.outstanding_amount)
		entry["invoices"].append({
			"name": inv.name, "due_date": inv.due_date,
			"outstanding": flt(inv.outstanding_amount), "bucket": bucket,
		})
	return sorted(out.values(), key=lambda r: -r["total"])


@frappe.whitelist()
def pay_supplier_invoice(purchase_invoice, amount=None, mode_of_payment=None):
	"""Pay against a bill, allocating a part payment when less than the total."""
	frappe.has_permission("Payment Entry", "create", throw=True)
	from erpnext.accounts.doctype.payment_entry.payment_entry import get_payment_entry

	pe = get_payment_entry("Purchase Invoice", purchase_invoice)
	# get_payment_entry leaves the amount unset when the company has no default
	# bank/cash account, and Payment Entry then refuses with "Paid Amount is
	# mandatory". Settle the whole outstanding unless a part payment was asked for.
	if not amount and not flt(pe.paid_amount):
		amount = flt(frappe.db.get_value("Purchase Invoice", purchase_invoice, "outstanding_amount"))
	# Only refuse when there is genuinely nothing to pay -- get_payment_entry may
	# already have filled the amount in, in which case `amount` is legitimately None.
	if not flt(amount) and not flt(pe.paid_amount):
		frappe.throw(
			_("{0} has nothing outstanding to pay. Check the rates on the order — a bill "
			  "totalling zero cannot be paid.").format(purchase_invoice)
		)
	if amount:
		pe.paid_amount = flt(amount)
		pe.received_amount = flt(amount)
		for ref in pe.references:
			ref.allocated_amount = min(flt(amount), flt(ref.outstanding_amount))
	if mode_of_payment:
		pe.mode_of_payment = mode_of_payment
	pe.flags.ignore_permissions = True
	pe.insert(ignore_permissions=True)
	frappe.db.commit()
	return {"name": pe.name}


@frappe.whitelist()
def submit_payment(name):
	return submit_document("Payment Entry", name)


# ---------------------------------------------------------------------------

def _has_field(doctype, fieldname):
	return bool(frappe.get_meta(doctype).get_field(fieldname))


@frappe.whitelist()
def buying_summary():
	return {
		"suppliers": frappe.db.count("Supplier"),
		"requisitions": status_counts("Material Request", filters={"material_request_type": "Purchase"}),
		"purchase_orders": status_counts("Purchase Order"),
		"purchase_invoices": status_counts("Purchase Invoice"),
	}


@frappe.whitelist()
def save_supplier(name=None, supplier_name=None, mobile_no=None, email_id=None, tax_id=None,
                  trade_license_expiry=None, vat_cert_expiry=None, establishment_expiry=None,
                  compliance_status=None):
	"""The Suppliers page pop-ups: create a supplier, or update the papers on one.

	The score is the share of the three papers that are on file and in date, so
	the number on the screen means one thing and is recomputed every time the
	dates change."""
	if name:
		frappe.has_permission("Supplier", "write", doc=name, throw=True)
		doc = frappe.get_doc("Supplier", name)
	else:
		frappe.has_permission("Supplier", "create", throw=True)
		supplier_name = (supplier_name or "").strip()
		if not supplier_name:
			frappe.throw(_("Supplier name is required"))
		if frappe.db.exists("Supplier", {"supplier_name": supplier_name}):
			frappe.throw(_("A supplier called {0} already exists.").format(supplier_name))
		doc = frappe.new_doc("Supplier")
		doc.supplier_name = supplier_name
		doc.supplier_group = (frappe.db.get_single_value("Buying Settings", "supplier_group")
		                      or frappe.db.get_value("Supplier Group", {"is_group": 0}, "name")
		                      or frappe.db.get_value("Supplier Group", {}, "name"))
		doc.supplier_type = "Company" if tax_id else "Individual"
		doc.country = doc.country or "United Arab Emirates"
	if mobile_no is not None:
		doc.mobile_no = mobile_no.strip() or None
	if email_id is not None:
		doc.email_id = email_id.strip() or None
	if tax_id is not None and tax_id.strip():
		doc.tax_id = tax_id.strip()

	dates = {
		"custom_compliance_trade_license_expiry": trade_license_expiry,
		"custom_compliance_vat_cert_expiry": vat_cert_expiry,
		"custom_compliance_establishment_expiry": establishment_expiry,
	}
	# A date not sent is kept; an empty string clears it. The pop-up sends all three.
	for field, value in dates.items():
		if value is None or not _has_field("Supplier", field):
			continue
		doc.set(field, getdate(value) if value else None)
	if compliance_status and _has_field("Supplier", "custom_compliance_status"):
		doc.custom_compliance_status = compliance_status
	if _has_field("Supplier", "custom_compliance_score"):
		today = getdate(nowdate())
		valid = sum(1 for f in dates if doc.get(f) and getdate(doc.get(f)) >= today)
		doc.custom_compliance_score = round(100.0 * valid / len(dates), 0)
	doc.save()
	return {"name": doc.name, "supplier_name": doc.supplier_name, "compliance": compliance_state(doc.as_dict())}


# ---------------------------------------------------------------------------
# Pop-ups on the buying screens
# ---------------------------------------------------------------------------

@frappe.whitelist()
def create_purchase_order(supplier, items, schedule_date=None, company=None):
	"""A purchase order from the pop-up: supplier, lines with rates, required-by
	date. Saved as a draft; the compliance gate runs on save, so a blocked
	supplier is refused here exactly as it would be anywhere else."""
	frappe.has_permission("Purchase Order", "create", throw=True)
	if isinstance(items, str):
		items = frappe.parse_json(items)
	items = [r for r in (items or []) if (r.get("item_code") or "").strip()]
	if not supplier:
		frappe.throw(_("Choose a supplier"))
	if not items:
		frappe.throw(_("Add at least one item to the order"))
	po = frappe.new_doc("Purchase Order")
	po.supplier = supplier
	po.company = company or default_company()
	po.transaction_date = nowdate()
	po.schedule_date = schedule_date or frappe.utils.add_days(nowdate(), 7)
	from a3_trading_management.api.live_stock import _mr_warehouse
	default_yard = _mr_warehouse()
	for row in items:
		po.append("items", {
			"item_code": row.get("item_code"),
			"qty": flt(row.get("qty")) or 1,
			"rate": flt(row.get("rate")),
			"warehouse": row.get("warehouse") or default_yard,
			"schedule_date": po.schedule_date,
		})
	po.set_missing_values()
	po.insert()
	frappe.db.commit()
	return {"name": po.name, "grand_total": po.grand_total}


@frappe.whitelist()
def update_order_rates(name, rows):
	"""Fix the rates on a draft order (an order made from a requisition carries
	none when the item has no price list rate)."""
	frappe.has_permission("Purchase Order", "write", doc=name, throw=True)
	po = frappe.get_doc("Purchase Order", name)
	if po.docstatus != 0:
		frappe.throw(_("{0} is already approved; its rates cannot be changed.").format(name))
	if isinstance(rows, str):
		rows = frappe.parse_json(rows)
	wanted = {r.get("name"): r for r in rows or []}
	for item in po.items:
		if item.name in wanted:
			item.rate = flt(wanted[item.name].get("rate"))
			if wanted[item.name].get("qty") is not None:
				item.qty = flt(wanted[item.name].get("qty")) or item.qty
	po.save()
	frappe.db.commit()
	return {"name": po.name, "grand_total": po.grand_total}


@frappe.whitelist()
def update_receipt_quantities(name, rows):
	"""What actually arrived: received and rejected per line on a draft receipt.
	Accepted quantity is received less rejected, the way ERPNext counts it."""
	frappe.has_permission("Purchase Receipt", "write", doc=name, throw=True)
	pr = frappe.get_doc("Purchase Receipt", name)
	if pr.docstatus != 0:
		frappe.throw(_("{0} is already posted; quantities cannot be changed.").format(name))
	if isinstance(rows, str):
		rows = frappe.parse_json(rows)
	wanted = {r.get("name"): r for r in rows or []}
	for item in pr.items:
		if item.name in wanted:
			received = flt(wanted[item.name].get("received_qty"))
			rejected = flt(wanted[item.name].get("rejected_qty"))
			if rejected > received:
				frappe.throw(_("Rejected cannot exceed received on {0}").format(item.item_code))
			item.received_qty = received
			item.rejected_qty = rejected
			item.qty = received - rejected
			# ERPNext keeps rejected material in its own yard, apart from good stock.
			if rejected and not item.rejected_warehouse:
				item.rejected_warehouse = _rejected_warehouse(pr.company)
	pr.save()
	frappe.db.commit()
	return {"name": pr.name}


def _rejected_warehouse(company):
	"""The yard rejected material is booked into -- created on first use."""
	existing = frappe.db.get_value("Warehouse", {"company": company, "is_group": 0, "warehouse_name": "Rejected"}, "name")
	if existing:
		return existing
	root = frappe.db.get_value("Warehouse", {"company": company, "is_group": 1, "parent_warehouse": ["in", ["", None]]}, "name")
	wh = frappe.get_doc({"doctype": "Warehouse", "warehouse_name": "Rejected", "company": company, "parent_warehouse": root})
	wh.insert(ignore_permissions=True)
	return wh.name
