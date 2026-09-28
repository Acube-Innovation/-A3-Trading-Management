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
	rows, status_counts, submit_document, cancel_document, default_company, ageing_bucket,
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
def receive_against_invoice(sales_invoice, amount=None, mode_of_payment=None):
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

@frappe.whitelist()
def list_deliveries(search=None, limit=200):
	return rows(
		"Delivery Note",
		["name", "customer", "customer_name", "posting_date", "status", "docstatus",
		 "grand_total", "currency"],
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
		 "docstatus", "grand_total", "outstanding_amount", "currency"],
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


def mark_delivered(doc, method=None):
	"""On submitting a Delivery Note, the trailers on it have left the yard."""
	serial = doc.get("custom_trailer_serial")
	if not serial:
		return
	if frappe.db.exists("Trailer Serial", serial):
		frappe.db.set_value("Trailer Serial", serial,
		                    {"status": "Delivered", "current_owner": doc.get("customer")},
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
	"""Delete a sales invoice, following the ledger's own rules on what may go.

	Permission-guarded and deliberately narrow: a SUBMITTED invoice has hit the
	ledger and is only removable once cancelled, which is ERPNext's rule, not one
	invented here. The caller must pass confirm=1 — the screen asks first.
	"""
	frappe.has_permission("Sales Invoice", "delete", throw=True)
	if not frappe.utils.cint(confirm):
		frappe.throw(_("Deleting {0} must be confirmed first.").format(name))

	docstatus = frappe.db.get_value("Sales Invoice", name, "docstatus")
	if docstatus is None:
		frappe.throw(_("Sales Invoice {0} does not exist").format(name))
	if docstatus == 1:
		frappe.throw(
			_("{0} is submitted and sits in the ledger. Cancel it first — a posted "
			  "invoice cannot simply be deleted.").format(name),
			title=_("Invoice is posted"),
		)

	# A cancelled invoice with payments still pointing at it must not leave those
	# payments dangling, so the ledger's own link check is left to run.
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
