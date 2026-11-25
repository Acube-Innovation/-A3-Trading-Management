# Implements: Phase 4 — journals, payments and receipts, sales orders, delivery
# and sales invoice, and the serial/chassis carried onto them (tasks 20-24).
"""The selling and accounting side of the trading portal.

The same stance as the buying screens: these present the standard ledger and the
standard sales documents. A trailer is dispatched and invoiced against its serial,
so the customer is billed for one identified trailer rather than a quantity.
"""

import frappe
from frappe import _
from frappe.utils import flt, nowdate, getdate, date_diff

from a3_trading_management.api._common import (
	rows, status_counts, submit_document, cancel_document, default_company, ageing_bucket, apply_taxes,
	set_payment_reference,
)


# ---------------------------------------------------------------------------
# Task 20 — Journal Entries (standard ledger; no parallel books)
# ---------------------------------------------------------------------------

@frappe.whitelist()
def list_journal_entries(search=None, limit=200):
	out = rows(
		"Journal Entry",
		["name", "posting_date", "voucher_type", "total_debit", "total_credit",
		 "docstatus", "user_remark", "company"],
		search=search, search_fields=["name", "user_remark"], limit=limit,
	)
	for r in out:
		r["accounts"] = frappe.get_all(
			"Journal Entry Account", filters={"parent": r["name"]},
			fields=["account", "debit_in_account_currency", "credit_in_account_currency",
			        "cost_center", "user_remark"],
		)
	return out


@frappe.whitelist()
def create_journal_entry(accounts, posting_date=None, voucher_type="Journal Entry",
                         user_remark=None, company=None):
	"""A manual journal, accrual or adjustment. Left as a draft — posting is the
	approval step, so nothing reaches the ledger without one."""
	frappe.has_permission("Journal Entry", "create", throw=True)
	if isinstance(accounts, str):
		accounts = frappe.parse_json(accounts)
	if not accounts:
		frappe.throw(_("A journal needs at least one line"))

	debit = sum(flt(a.get("debit")) for a in accounts)
	credit = sum(flt(a.get("credit")) for a in accounts)
	if flt(debit, 2) != flt(credit, 2):
		frappe.throw(_("Journal does not balance: debit {0} against credit {1}").format(debit, credit))

	je = frappe.new_doc("Journal Entry")
	je.voucher_type = voucher_type
	je.posting_date = posting_date or nowdate()
	je.company = company or default_company()
	je.user_remark = user_remark
	for line in accounts:
		je.append("accounts", {
			"account": line.get("account"),
			"debit_in_account_currency": flt(line.get("debit")),
			"credit_in_account_currency": flt(line.get("credit")),
			"cost_center": line.get("cost_center"),
			"user_remark": line.get("remark"),
		})
	je.insert(ignore_permissions=True)
	frappe.db.commit()
	return {"name": je.name}


@frappe.whitelist()
def post_journal_entry(name):
	"""The approval step — submitting is what posts it."""
	return submit_document("Journal Entry", name)


# ---------------------------------------------------------------------------
# Task 21 — Payments and Receipts
# ---------------------------------------------------------------------------

@frappe.whitelist()
def list_payments(search=None, payment_type=None, party_type=None, limit=200):
	filters = {}
	if payment_type:
		filters["payment_type"] = payment_type
	if party_type:
		filters["party_type"] = party_type
	return rows(
		"Payment Entry",
		["name", "payment_type", "party_type", "party", "party_name", "posting_date",
		 "paid_amount", "received_amount", "unallocated_amount", "status", "docstatus",
		 "mode_of_payment"],
		filters=filters, search=search, search_fields=["name", "party", "party_name"], limit=limit,
	)


@frappe.whitelist()
def receive_against_invoice(sales_invoice, amount=None, mode_of_payment=None, reference_no=None, reference_date=None):
	"""Money in, allocated against the invoice it settles."""
	frappe.has_permission("Payment Entry", "create", throw=True)
	from erpnext.accounts.doctype.payment_entry.payment_entry import get_payment_entry

	pe = get_payment_entry("Sales Invoice", sales_invoice)
	if not amount and not flt(pe.received_amount):
		amount = flt(frappe.db.get_value("Sales Invoice", sales_invoice, "outstanding_amount"))
	if amount:
		pe.paid_amount = flt(amount)
		pe.received_amount = flt(amount)
		for ref in pe.references:
			ref.allocated_amount = min(flt(amount), flt(ref.outstanding_amount))
	if mode_of_payment:
		pe.mode_of_payment = mode_of_payment
	set_payment_reference(pe, sales_invoice, reference_no, reference_date)
	pe.flags.ignore_permissions = True
	pe.insert(ignore_permissions=True)
	frappe.db.commit()
	return {"name": pe.name}


@frappe.whitelist()
def receivables():
	"""What customers owe, by age — the mirror of supplier_outstanding."""
	frappe.has_permission("Sales Invoice", "read", throw=True)
	today = getdate(nowdate())
	out = {}
	for inv in frappe.get_all(
		"Sales Invoice", filters={"docstatus": 1, "outstanding_amount": [">", 0]},
		fields=["customer", "customer_name", "name", "due_date", "outstanding_amount", "currency"],
	):
		entry = out.setdefault(inv.customer, {
			"customer": inv.customer, "customer_name": inv.customer_name,
			"currency": inv.currency, "total": 0,
			"buckets": {"Not due": 0, "1-30": 0, "31-60": 0, "61-90": 0, "90+": 0},
		})
		days = date_diff(today, getdate(inv.due_date)) if inv.due_date else 0
		entry["total"] += flt(inv.outstanding_amount)
		entry["buckets"][ageing_bucket(days)] += flt(inv.outstanding_amount)
	return sorted(out.values(), key=lambda r: -r["total"])


# ---------------------------------------------------------------------------
# Task 22 — Sales Orders, with serial reservation or a work order to build one
# ---------------------------------------------------------------------------

@frappe.whitelist()
def list_sales_orders(search=None, status=None, limit=200):
	filters = {}
	if status:
		filters["status"] = status
	out = rows(
		"Sales Order",
		["name", "customer", "customer_name", "transaction_date", "delivery_date",
		 "status", "docstatus", "grand_total", "currency", "per_delivered", "per_billed"],
		filters=filters, search=search, search_fields=["name", "customer", "customer_name"],
		limit=limit,
	)
	for r in out:
		r["reserved_serials"] = frappe.get_all(
			"Trailer Serial", filters={"sales_order": r["name"]},
			fields=["name", "chassis_number", "status"],
		) if _has_serial_field("sales_order") else []
	return out


@frappe.whitelist()
def available_serials(trailer_type=None):
	"""Finished trailers sitting in stock, free to be sold."""
	frappe.has_permission("Trailer Serial", "read", throw=True)
	filters = {"status": "In Stock"}
	if trailer_type:
		filters["trailer_type"] = trailer_type
	return frappe.get_all(
		"Trailer Serial", filters=filters,
		fields=["name", "serial_no", "chassis_number", "trailer_type", "warehouse"],
		order_by="creation asc",
	)


@frappe.whitelist()
def reserve_serial(sales_order, serial):
	"""Reserve a specific trailer from stock against a customer order."""
	frappe.has_permission("Trailer Serial", "write", throw=True)
	status = frappe.db.get_value("Trailer Serial", serial, "status")
	if status != "In Stock":
		frappe.throw(_("Trailer {0} is {1}, so it cannot be reserved.").format(serial, status))
	if _has_serial_field("sales_order"):
		frappe.db.set_value("Trailer Serial", serial,
		                    {"sales_order": sales_order, "status": "Sold"}, update_modified=False)
	else:
		frappe.db.set_value("Trailer Serial", serial, "status", "Sold", update_modified=False)
	frappe.db.commit()
	return {"serial": serial, "sales_order": sales_order}


@frappe.whitelist()
def build_for_order(sales_order, item_code, qty=1):
	"""No trailer in stock: raise a work order for this order. It mints the serials
	now and hands them to the order when the build completes."""
	frappe.has_permission("Work Order", "create", throw=True)
	from a3_trading_management.api.manufacturing import create_work_order

	result = create_work_order(item=item_code, qty=qty, sales_order=sales_order)
	frappe.db.commit()
	return {"work_order": result["name"]}


@frappe.whitelist()
def submit_sales_order(name):
	return submit_document("Sales Order", name)


# ---------------------------------------------------------------------------
# Tasks 23 and 24 — dispatch against the serial, and invoice it
# ---------------------------------------------------------------------------

def _chassis_field(doctype):
	"""The chassis number column, where the custom field is on this site."""
	return ["custom_chassis_number"] if frappe.get_meta(doctype).has_field("custom_chassis_number") else []


@frappe.whitelist()
def list_deliveries(search=None, limit=200):
	return rows(
		"Delivery Note",
		["name", "customer", "customer_name", "posting_date", "status", "docstatus",
		 "grand_total", "currency", "per_billed"] + _chassis_field("Delivery Note"),
		search=search, search_fields=["name", "customer", "customer_name"], limit=limit,
	)


@frappe.whitelist()
def list_sales_invoices(search=None, status=None, limit=200):
	filters = {}
	if status:
		filters["status"] = status
	return rows(
		"Sales Invoice",
		["name", "customer", "customer_name", "posting_date", "due_date", "status",
		 "docstatus", "grand_total", "outstanding_amount", "currency"] + _chassis_field("Sales Invoice"),
		filters=filters, search=search, search_fields=["name", "customer", "customer_name"],
		limit=limit,
	)


@frappe.whitelist()
def dispatch_serial(sales_order, serials=None):
	"""Dispatch a trailer against its serial: a Delivery Note carrying the chassis
	number, so what leaves the yard is an identified unit."""
	frappe.has_permission("Delivery Note", "create", throw=True)
	from erpnext.selling.doctype.sales_order.sales_order import make_delivery_note

	dn = make_delivery_note(sales_order)
	if isinstance(serials, str):
		serials = frappe.parse_json(serials)
	serials = serials or []
	if serials:
		_stamp_serial_fields(dn, serials)
	apply_taxes(dn)
	dn.flags.ignore_permissions = True
	dn.insert(ignore_permissions=True)
	frappe.db.commit()
	return {"name": dn.name}


def _stock_by_warehouse(item_code):
	return {b.warehouse: flt(b.actual_qty) for b in frappe.get_all(
		"Bin", filters={"item_code": item_code, "actual_qty": [">", 0]}, fields=["warehouse", "actual_qty"])}


def _pick_warehouse(item_code, qty, preferred=None):
	"""Where `qty` of the item can come from: the order's warehouse when it holds
	enough, else the warehouse holding the most. None when no warehouse has enough
	(negative stock is allowed on this site, so ERPNext would not stop it)."""
	stock = _stock_by_warehouse(item_code)
	if preferred and stock.get(preferred, 0) >= qty:
		return preferred
	best = max(stock.items(), key=lambda kv: kv[1], default=(None, 0))
	return best[0] if best[1] >= qty else None


@frappe.whitelist()
def order_stock(sales_order):
	"""What an order still has to deliver and whether stock covers it, for a
	trailer held in stock without a serial (opening stock)."""
	frappe.has_permission("Sales Order", "read", doc=sales_order, throw=True)
	so = frappe.get_doc("Sales Order", sales_order)
	lines = []
	for it in so.items:
		pending = flt(it.stock_qty) - flt(it.delivered_qty) * flt(it.conversion_factor or 1)
		if pending <= 0 or not frappe.get_cached_value("Item", it.item_code, "is_stock_item"):
			continue
		stock = _stock_by_warehouse(it.item_code)
		lines.append({"item_code": it.item_code, "item_name": it.item_name, "pending": pending,
		              "in_stock": sum(stock.values()), "warehouse": _pick_warehouse(it.item_code, pending, it.warehouse)})
	return {"docstatus": so.docstatus, "lines": lines,
	        "covered": bool(lines) and all(l["warehouse"] for l in lines)}


@frappe.whitelist()
def dispatch_from_stock(sales_order):
	"""A draft Delivery Note for an order, taken from stock without a serial: each
	line from a warehouse that actually holds it."""
	frappe.has_permission("Delivery Note", "create", throw=True)
	from erpnext.selling.doctype.sales_order.sales_order import make_delivery_note

	dn = make_delivery_note(sales_order)
	for it in dn.items:
		if not frappe.get_cached_value("Item", it.item_code, "is_stock_item"):
			continue
		wh = _pick_warehouse(it.item_code, flt(it.stock_qty) or flt(it.qty), it.warehouse)
		if not wh:
			frappe.throw(_("{0}: not enough in stock to dispatch {1}.").format(it.item_name or it.item_code, it.qty))
		it.warehouse = wh
	apply_taxes(dn)
	dn.flags.ignore_permissions = True
	dn.insert(ignore_permissions=True)
	frappe.db.commit()
	return {"name": dn.name}


@frappe.whitelist()
def invoice_delivery(delivery_note):
	"""Invoice the dispatch, carrying the serial and chassis onto the bill."""
	frappe.has_permission("Sales Invoice", "create", throw=True)
	from erpnext.stock.doctype.delivery_note.delivery_note import make_sales_invoice

	si = make_sales_invoice(delivery_note)
	carried = frappe.db.get_value(
		"Delivery Note", delivery_note, ["custom_trailer_serial", "custom_chassis_number"],
		as_dict=True
	) or {}
	if carried.get("custom_trailer_serial") and _has_field("Sales Invoice", "custom_trailer_serial"):
		si.custom_trailer_serial = carried["custom_trailer_serial"]
		si.custom_chassis_number = carried.get("custom_chassis_number")
	# The mapper copies the Delivery Note's unchecked value; invoices default to no rounding.
	si.disable_rounded_total = 1
	apply_taxes(si)
	si.flags.ignore_permissions = True
	si.insert(ignore_permissions=True)
	frappe.db.commit()
	return {"name": si.name}


def _stamp_serial_fields(doc, serials):
	"""Carry the serial and chassis number onto the document (task 24)."""
	if not serials:
		return
	first = serials[0]
	chassis = frappe.db.get_value("Trailer Serial", first, "chassis_number")
	if _has_field(doc.doctype, "custom_trailer_serial"):
		doc.custom_trailer_serial = first
		doc.custom_chassis_number = chassis
	# More than one trailer on the note: record them all in the remarks so the
	# printed note names every unit, not just the first.
	if len(serials) > 1:
		names = frappe.get_all("Trailer Serial", filters={"name": ["in", serials]},
		                       pluck="chassis_number")
		doc.remarks = (doc.get("remarks") or "") + "\n" + _("Chassis: {0}").format(", ".join(names))


def delivered_serials(doc):
	"""Every Trailer Serial on a Delivery Note: the serial field, plus each chassis
	number listed (a note for several trailers keeps only the first serial in the
	field and all the chassis numbers, comma-separated)."""
	serials = []
	first = doc.get("custom_trailer_serial")
	if first and frappe.db.exists("Trailer Serial", first):
		serials.append(first)
	for chassis in (doc.get("custom_chassis_number") or "").replace("\n", ",").split(","):
		chassis = chassis.strip()
		if not chassis:
			continue
		name = frappe.db.get_value("Trailer Serial", {"chassis_number": chassis}, "name")
		if name and name not in serials:
			serials.append(name)
	return serials


def mark_delivered(doc, method=None):
	"""On submitting a Delivery Note, the trailers on it have left the yard: status
	Delivered, owned by the customer, no longer in a warehouse of ours."""
	for serial in delivered_serials(doc):
		frappe.db.set_value("Trailer Serial", serial,
		                    {"status": "Delivered", "current_owner": doc.get("customer"), "warehouse": None},
		                    update_modified=False)


def unmark_delivered(doc, method=None):
	"""A cancelled Delivery Note puts its trailers back in the yard: Sold when they
	are still on a sales order, else In Stock, in the warehouse they left from."""
	yard = next((i.warehouse for i in doc.get("items") or [] if i.get("warehouse")), None)
	for serial in delivered_serials(doc):
		so = frappe.db.get_value("Trailer Serial", serial, "sales_order")
		frappe.db.set_value("Trailer Serial", serial,
		                    {"status": "Sold" if so else "In Stock", "current_owner": None, "warehouse": yard},
		                    update_modified=False)


def _has_field(doctype, fieldname):
	try:
		return bool(frappe.get_meta(doctype).get_field(fieldname))
	except Exception:
		return False


def _has_serial_field(fieldname):
	return _has_field("Trailer Serial", fieldname)


@frappe.whitelist()
def selling_summary():
	return {
		"sales_orders": status_counts("Sales Order"),
		"sales_invoices": status_counts("Sales Invoice"),
		"deliveries": status_counts("Delivery Note"),
		"in_stock_trailers": frappe.db.count("Trailer Serial", {"status": "In Stock"}),
	}


# ---------------------------------------------------------------------------
# Task 28 — sales invoice delete action (EXTRA POINT 3)
# ---------------------------------------------------------------------------

@frappe.whitelist()
def delete_sales_invoice(name, confirm=None):
	"""Delete a DRAFT sales invoice.

	Permission-guarded and deliberately narrow: a posted (or cancelled) invoice
	has ledger entries and is removed only through an approved Invoice Deletion
	Request. The caller must pass confirm=1 — the screen asks first.
	"""
	frappe.has_permission("Sales Invoice", "delete", throw=True)
	if not frappe.utils.cint(confirm):
		frappe.throw(_("Deleting {0} must be confirmed first.").format(name))

	docstatus = frappe.db.get_value("Sales Invoice", name, "docstatus")
	if docstatus is None:
		frappe.throw(_("Sales Invoice {0} does not exist").format(name))
	if docstatus != 0:
		# A posted or cancelled invoice still has ledger entries; those go only
		# through an approved Invoice Deletion Request (api.invoice_delete).
		frappe.throw(
			_("{0} has been posted to the ledger. Use Request Delete — Finance and the "
			  "Owner must approve before it and its ledger entries are removed.").format(name),
			title=_("Invoice is posted"),
		)

	frappe.delete_doc("Sales Invoice", name, ignore_permissions=False)
	frappe.db.commit()
	return {"deleted": name}


# ---------------------------------------------------------------------------
# Pop-ups on the selling screens
# ---------------------------------------------------------------------------

@frappe.whitelist()
def create_sales_order(customer, items, delivery_date=None, company=None):
	"""A sales order from the pop-up: customer, lines with rates, delivery date.
	Saved as a draft; Confirm on the list is the approval."""
	frappe.has_permission("Sales Order", "create", throw=True)
	if isinstance(items, str):
		items = frappe.parse_json(items)
	items = [r for r in (items or []) if (r.get("item_code") or "").strip()]
	if not customer:
		frappe.throw(_("Choose a customer"))
	if not items:
		frappe.throw(_("Add at least one item to the order"))
	so = frappe.new_doc("Sales Order")
	so.customer = customer
	so.company = company or default_company()
	so.order_type = "Sales"
	so.transaction_date = nowdate()
	so.delivery_date = delivery_date or frappe.utils.add_days(nowdate(), 7)
	price_list = (frappe.db.get_single_value("Selling Settings", "selling_price_list")
	              or frappe.db.get_value("Price List", {"selling": 1, "enabled": 1}, "name"))
	if price_list:
		so.selling_price_list = price_list
	fg = frappe.db.get_value("Warehouse", {"warehouse_name": "Finished Goods", "is_group": 0}, "name")
	for row in items:
		is_trailer = frappe.db.get_value("Item", row.get("item_code"), "custom_is_trailer")
		so.append("items", {
			"item_code": row.get("item_code"),
			"qty": flt(row.get("qty")) or 1,
			"rate": flt(row.get("rate")),
			"delivery_date": so.delivery_date,
			"warehouse": (fg if is_trailer else None) or row.get("warehouse") or None,
		})
	so.set_missing_values()
	apply_taxes(so)
	so.insert()
	frappe.db.commit()
	return {"name": so.name, "grand_total": so.grand_total}


# ---------------------------------------------------------------------------
# Quotations, priced by Sales on the portal
#
# The Supervisor drafts a quotation without prices (supervisor_portal); Sales
# makes priced ones here, opens the Supervisor's drafts to price them, approves
# them (submit, Super Admin only through submit_guard) and turns an accepted
# one into a Sales Order. This module is not on the Supervisor's API allowlist,
# so prices never reach them.
# ---------------------------------------------------------------------------

@frappe.whitelist()
def list_quotations(search=None, limit=200):
	frappe.has_permission("Quotation", "read", throw=True)
	quotes = rows(
		"Quotation",
		["name", "party_name", "customer_name", "transaction_date", "valid_till",
		 "status", "docstatus", "grand_total", "currency", "owner"],
		filters={"quotation_to": "Customer"}, search=search,
		search_fields=["name", "party_name", "customer_name"], limit=limit,
		order_by="transaction_date desc, modified desc",
	)
	items = {}
	for i in frappe.get_all("Quotation Item", filters={"parent": ["in", [q["name"] for q in quotes] or [""]]},
	                        fields=["parent", "item_code", "item_name", "qty", "rate"], order_by="idx"):
		items.setdefault(i.parent, []).append(i)
	for q in quotes:
		q["items"] = items.get(q["name"], [])
		# A Supervisor's draft still waiting for prices.
		q["unpriced"] = q["docstatus"] == 0 and any(not flt(i.rate) for i in q["items"])
	return quotes


@frappe.whitelist()
def create_quotation(customer, items, valid_till=None, company=None):
	"""A priced quotation from the pop-up, saved as a draft; Approve on the list submits it."""
	frappe.has_permission("Quotation", "create", throw=True)
	if isinstance(items, str):
		items = frappe.parse_json(items)
	items = [r for r in (items or []) if (r.get("item_code") or "").strip() and flt(r.get("qty")) > 0]
	if not customer:
		frappe.throw(_("Choose a customer"))
	if not items:
		frappe.throw(_("Add at least one item with a quantity"))
	qt = frappe.new_doc("Quotation")
	qt.quotation_to = "Customer"
	qt.party_name = customer
	qt.company = company or default_company()
	qt.order_type = "Sales"
	qt.transaction_date = nowdate()
	qt.valid_till = valid_till or frappe.utils.add_days(nowdate(), 30)
	price_list = (frappe.db.get_single_value("Selling Settings", "selling_price_list")
	              or frappe.db.get_value("Price List", {"selling": 1, "enabled": 1}, "name"))
	if price_list:
		qt.selling_price_list = price_list
	for row in items:
		qt.append("items", {"item_code": row.get("item_code"), "qty": flt(row.get("qty")), "rate": flt(row.get("rate"))})
	qt.set_missing_values()
	apply_taxes(qt)
	qt.insert()
	frappe.db.commit()
	return {"name": qt.name, "grand_total": qt.grand_total}


@frappe.whitelist()
def price_quotation(name, rates):
	"""Put the rates on a draft quotation (typically one a Supervisor raised).
	`rates` maps the Quotation Item row name to its rate."""
	if isinstance(rates, str):
		rates = frappe.parse_json(rates)
	qt = frappe.get_doc("Quotation", name)
	qt.check_permission("write")
	if qt.docstatus != 0:
		frappe.throw(_("Quotation {0} is already approved").format(name))
	for row in qt.items:
		if row.name in (rates or {}):
			row.rate = flt(rates[row.name])
			# The rate typed in wins: no discount or margin re-derives it from the price list.
			row.discount_percentage = 0
			row.discount_amount = 0
			row.margin_type = None
			row.margin_rate_or_amount = 0
	if not qt.get("taxes"):
		apply_taxes(qt)
	qt.save()
	frappe.db.commit()
	return {"name": qt.name, "grand_total": qt.grand_total}


@frappe.whitelist()
def get_quotation_lines(name):
	frappe.has_permission("Quotation", "read", doc=name, throw=True)
	return frappe.get_all("Quotation Item", filters={"parent": name, "parenttype": "Quotation"},
	                      fields=["name", "item_code", "item_name", "qty", "rate"], order_by="idx")


@frappe.whitelist()
def submit_quotation(name):
	if any(not flt(r) for r in frappe.get_all("Quotation Item", filters={"parent": name}, pluck="rate")):
		frappe.throw(_("Every line of {0} needs a rate before it is approved").format(name))
	return submit_document("Quotation", name)


@frappe.whitelist()
def make_sales_order_from_quotation(name, delivery_date=None):
	"""The customer accepted: a draft Sales Order from the approved quotation, mapped the
	standard way so terms, print sections and taxes come across."""
	frappe.has_permission("Sales Order", "create", throw=True)
	from erpnext.selling.doctype.quotation.quotation import make_sales_order

	if frappe.db.get_value("Quotation", name, "docstatus") != 1:
		frappe.throw(_("Approve quotation {0} before turning it into an order").format(name))
	so = make_sales_order(name)
	so.transaction_date = nowdate()
	so.delivery_date = delivery_date or frappe.utils.add_days(nowdate(), 7)
	fg = frappe.db.get_value("Warehouse", {"warehouse_name": "Finished Goods", "is_group": 0}, "name")
	for row in so.items:
		row.delivery_date = row.delivery_date or so.delivery_date
		if fg and frappe.db.get_value("Item", row.item_code, "custom_is_trailer"):
			row.warehouse = fg
	so.insert()
	frappe.db.commit()
	return {"name": so.name, "grand_total": so.grand_total}


@frappe.whitelist()
def submit_delivery_note(name):
	"""The trailer has left the yard: posts the note (stock out, serial Delivered)."""
	frappe.has_permission("Delivery Note", "submit", doc=name, throw=True)
	return submit_document("Delivery Note", name)


@frappe.whitelist()
def submit_sales_invoice(name):
	"""Posts the invoice to the ledger; Receive becomes available."""
	frappe.has_permission("Sales Invoice", "submit", doc=name, throw=True)
	return submit_document("Sales Invoice", name)


# ---------------------------------------------------------------------------
# Sales invoices raised on the portal: direct, or from a Sales Order
# ---------------------------------------------------------------------------

def _selling_price_list():
	return (frappe.db.get_single_value("Selling Settings", "selling_price_list")
	        or frappe.db.get_value("Price List", {"selling": 1, "enabled": 1}, "name"))


@frappe.whitelist()
def create_sales_invoice(customer, items, due_date=None, company=None):
	"""A sales invoice from the pop-up, without an order: parts, services, transport.
	Stock items leave the yard on the line (Update Stock). A trailer is not sold
	this way: it is invoiced from its Sales Order or Delivery Note, which follow
	the serial. Saved as a draft; Open & Submit on the list posts it."""
	frappe.has_permission("Sales Invoice", "create", throw=True)
	if isinstance(items, str):
		items = frappe.parse_json(items)
	items = [r for r in (items or []) if (r.get("item_code") or "").strip()]
	if not customer:
		frappe.throw(_("Choose a customer"))
	if not items:
		frappe.throw(_("Add at least one item to the invoice"))
	si = frappe.new_doc("Sales Invoice")
	si.customer = customer
	si.company = company or default_company()
	si.posting_date = nowdate()
	si.disable_rounded_total = 1
	price_list = _selling_price_list()
	if price_list:
		si.selling_price_list = price_list
	any_stock = False
	for row in items:
		item = frappe.get_cached_value("Item", row["item_code"], ["is_stock_item", "custom_is_trailer"], as_dict=True)
		if not item:
			frappe.throw(_("Item {0} not found").format(row["item_code"]))
		if item.get("custom_is_trailer"):
			frappe.throw(_("{0} is a trailer: invoice it from its Sales Order or Delivery Note, so the serial goes with it.").format(row["item_code"]))
		if flt(row.get("qty")) <= 0:
			frappe.throw(_("{0}: enter a quantity").format(row["item_code"]))
		any_stock = any_stock or bool(item.is_stock_item)
		si.append("items", {
			"item_code": row["item_code"], "qty": flt(row.get("qty")), "rate": flt(row.get("rate")),
			"warehouse": row.get("warehouse") or None,
		})
	si.update_stock = 1 if any_stock else 0
	si.set_missing_values()
	if due_date:
		si.due_date = getdate(due_date)
		si.payment_schedule = []
	apply_taxes(si)
	si.insert()
	frappe.db.commit()
	return {"name": si.name, "grand_total": si.grand_total}


def _so_invoice(sales_order):
	"""The standard Sales Order -> Sales Invoice mapping: every line with an
	amount still to bill, at the quantity still to bill."""
	from erpnext.selling.doctype.sales_order.sales_order import make_sales_invoice

	so = frappe.db.get_value("Sales Order", sales_order, ["docstatus", "status"], as_dict=True)
	if not so or so.docstatus != 1:
		frappe.throw(_("Confirm Sales Order {0} before invoicing it").format(sales_order))
	if so.status in ("Closed", "On Hold"):
		frappe.throw(_("Sales Order {0} is {1}").format(sales_order, so.status))
	return make_sales_invoice(sales_order)


@frappe.whitelist()
def get_so_billable(sales_order):
	"""What the Make Invoice pop-up offers: each line still to bill."""
	frappe.has_permission("Sales Order", "read", doc=sales_order, throw=True)
	si = _so_invoice(sales_order)
	ordered = {r.name: r for r in frappe.get_all("Sales Order Item", filters={"parent": sales_order},
	                                             fields=["name", "qty", "delivered_qty"])}
	return [{"so_detail": i.so_detail, "item_code": i.item_code, "item_name": i.item_name, "uom": i.uom,
	         "rate": i.rate, "to_bill": i.qty, "ordered": ordered[i.so_detail].qty,
	         "delivered": ordered[i.so_detail].delivered_qty} for i in si.items if i.so_detail in ordered]


@frappe.whitelist()
def make_invoice_from_so(sales_order, lines=None):
	"""A draft invoice from a confirmed Sales Order. `lines` maps a Sales Order Item
	to the quantity to bill now (any part of what is still to bill); without it
	everything still to bill goes on."""
	frappe.has_permission("Sales Invoice", "create", throw=True)
	si = _so_invoice(sales_order)
	if isinstance(lines, str):
		lines = frappe.parse_json(lines)
	if lines:
		keep = []
		for row in si.items:
			want = flt(lines.get(row.so_detail))
			if want <= 0:
				continue
			if want > flt(row.qty) + 1e-9:
				frappe.throw(_("{0}: only {1} left to bill on {2}").format(row.item_code, frappe.format(row.qty), sales_order))
			row.qty = want
			keep.append(row)
		if not keep:
			frappe.throw(_("Enter a quantity to bill on at least one line"))
		si.set("items", keep)
		for i, row in enumerate(si.items, 1):
			row.idx = i
	if not si.items:
		frappe.throw(_("Nothing is left to bill on {0}").format(sales_order))
	si.disable_rounded_total = 1
	# The order's schedule is for its whole amount; this bill's is worked out afresh.
	si.payment_schedule = []
	si.calculate_taxes_and_totals()
	apply_taxes(si)
	si.insert()
	frappe.db.commit()
	return {"name": si.name, "grand_total": si.grand_total}
