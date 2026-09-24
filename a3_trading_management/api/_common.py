# Implements: shared read/action helpers behind the Phase 3 and 4 portal screens.
"""Common ground for the trading portal screens.

Every screen in Phases 3 and 4 is a view onto a document ERPNext already posts —
Purchase Order, Purchase Receipt, Payment Entry, Journal Entry, Sales Order,
Delivery Note, Sales Invoice. Nothing here keeps its own ledger or its own copy of
a balance: the scope is explicit that no parallel books are built, so each screen
reads the real document and each action calls the real submit.
"""

import frappe
from frappe import _
from frappe.utils import cint, flt


def rows(doctype, fields, filters=None, search=None, search_fields=None,
         limit=200, order_by="modified desc"):
	"""A permission-checked list read, with an optional `like` across `search_fields`."""
	frappe.has_permission(doctype, "read", throw=True)
	or_filters = None
	if search and search_fields:
		like = f"%{search}%"
		or_filters = {f: ["like", like] for f in search_fields}
	return frappe.get_all(
		doctype,
		fields=fields,
		filters=filters or {},
		or_filters=or_filters,
		limit_page_length=cint(limit) or 200,
		order_by=order_by,
	)


def status_counts(doctype, field="status", filters=None):
	out = {}
	for r in frappe.get_all(
		doctype, fields=[field, "count(name) as n"], filters=filters or {}, group_by=field
	):
		out[r.get(field) or "—"] = r.n
	return out


def submit_document(doctype, name):
	"""Submit a draft through the document's own controller.

	Deliberately `doc.submit()` rather than a status write: submitting a Purchase
	Invoice is what posts it to the ledger, and short-cutting that would be exactly
	the parallel book the scope rules out.
	"""
	frappe.has_permission(doctype, "submit", throw=True)
	doc = frappe.get_doc(doctype, name)
	if doc.docstatus != 0:
		frappe.throw(_("{0} {1} is not a draft").format(_(doctype), name))
	doc.submit()
	frappe.db.commit()
	return {"name": doc.name, "docstatus": doc.docstatus, "status": doc.get("status")}


def cancel_document(doctype, name):
	frappe.has_permission(doctype, "cancel", throw=True)
	doc = frappe.get_doc(doctype, name)
	doc.cancel()
	frappe.db.commit()
	return {"name": doc.name, "docstatus": doc.docstatus, "status": doc.get("status")}


def default_company():
	return (
		frappe.defaults.get_user_default("Company")
		or frappe.db.get_single_value("Global Defaults", "default_company")
		or (frappe.get_all("Company", pluck="name", limit=1) or [None])[0]
	)


def money(value, currency=None):
	return frappe.utils.fmt_money(flt(value), currency=currency or _currency())


def _currency():
	company = default_company()
	return company and frappe.db.get_value("Company", company, "default_currency") or ""


def ageing_bucket(days):
	"""Standard ageing buckets, used by the supplier and receivable screens."""
	days = cint(days)
	if days <= 0:
		return "Not due"
	if days <= 30:
		return "1-30"
	if days <= 60:
		return "31-60"
	if days <= 90:
		return "61-90"
	return "90+"


# ---------------------------------------------------------------------------
# Pop-ups: the document viewer and the pickers the line editors use
# ---------------------------------------------------------------------------

# What the portal's document viewer may show, and which fields it reads. Anything
# not listed here cannot be opened through it.
VIEWABLE = {
	"Material Request": {"party": None, "lines": "items",
		"line_fields": ["item_code", "item_name", "qty", "uom", "warehouse", "schedule_date", "ordered_qty", "received_qty"],
		"head": ["transaction_date", "schedule_date", "material_request_type"]},
	"Purchase Order": {"party": "supplier", "party_name": "supplier_name", "lines": "items",
		"line_fields": ["item_code", "item_name", "qty", "uom", "rate", "amount", "warehouse", "received_qty", "billed_amt", "schedule_date"],
		"head": ["transaction_date", "schedule_date", "grand_total", "per_received", "per_billed"]},
	"Purchase Receipt": {"party": "supplier", "party_name": "supplier_name", "lines": "items",
		"line_fields": ["item_code", "item_name", "received_qty", "rejected_qty", "qty", "uom", "rate", "amount", "warehouse", "purchase_order"],
		"head": ["posting_date", "grand_total", "per_billed"]},
	"Purchase Invoice": {"party": "supplier", "party_name": "supplier_name", "lines": "items",
		"line_fields": ["item_code", "item_name", "qty", "uom", "rate", "amount", "purchase_receipt"],
		"head": ["posting_date", "due_date", "grand_total", "outstanding_amount", "custom_approval_hold", "custom_hold_reason"]},
	"Sales Order": {"party": "customer", "party_name": "customer_name", "lines": "items",
		"line_fields": ["item_code", "item_name", "qty", "uom", "rate", "amount", "warehouse", "delivered_qty", "billed_amt", "delivery_date"],
		"head": ["transaction_date", "delivery_date", "grand_total", "per_delivered", "per_billed"]},
	"Delivery Note": {"party": "customer", "party_name": "customer_name", "lines": "items",
		"line_fields": ["item_code", "item_name", "qty", "uom", "rate", "amount", "warehouse", "against_sales_order"],
		"head": ["posting_date", "grand_total", "custom_trailer_serial", "custom_chassis_number", "remarks", "per_billed"]},
	"Sales Invoice": {"party": "customer", "party_name": "customer_name", "lines": "items",
		"line_fields": ["item_code", "item_name", "qty", "uom", "rate", "amount", "delivery_note"],
		"head": ["posting_date", "due_date", "grand_total", "outstanding_amount", "custom_trailer_serial", "custom_chassis_number"]},
	"Payment Entry": {"party": "party", "party_name": "party_name", "lines": "references",
		"line_fields": ["reference_doctype", "reference_name", "total_amount", "outstanding_amount", "allocated_amount"],
		"head": ["posting_date", "payment_type", "party_type", "mode_of_payment", "paid_from", "paid_to", "paid_amount", "received_amount", "reference_no"]},
	"Journal Entry": {"party": None, "lines": "accounts",
		"line_fields": ["account", "party_type", "party", "debit_in_account_currency", "credit_in_account_currency", "user_remark"],
		"head": ["posting_date", "voucher_type", "total_debit", "total_credit", "user_remark"]},
}


@frappe.whitelist()
def get_document(doctype, name):
	"""One document, read for the portal's viewer pop-up: its header fields, its
	lines, and whether it is still a draft."""
	if doctype not in VIEWABLE:
		frappe.throw(_("{0} cannot be shown here").format(doctype))
	frappe.has_permission(doctype, "read", doc=name, throw=True)
	doc = frappe.get_doc(doctype, name)
	spec = VIEWABLE[doctype]
	head = {f: doc.get(f) for f in spec["head"] if doc.meta.has_field(f)}
	lines = []
	for r in doc.get(spec["lines"]) or []:
		row = {f: r.get(f) for f in spec["line_fields"] if r.meta.has_field(f)}
		row["name"] = r.name
		lines.append(row)
	return {
		"doctype": doctype, "name": doc.name, "docstatus": doc.docstatus,
		"status": doc.get("status") or ("Draft" if doc.docstatus == 0 else "Submitted" if doc.docstatus == 1 else "Cancelled"),
		"party": doc.get(spec["party"]) if spec["party"] else None,
		"party_name": doc.get(spec.get("party_name")) if spec.get("party_name") else None,
		"currency": doc.get("currency") or _currency(),
		"head": head, "lines": lines, "editable": doc.docstatus == 0,
	}


@frappe.whitelist()
def search_items(search=None, price_list=None, limit=15):
	"""Stock items for a line editor, with the price from `price_list` when given."""
	frappe.has_permission("Item", "read", throw=True)
	like = "%{0}%".format((search or "").strip())
	items = frappe.get_all(
		"Item", filters={"disabled": 0, "is_stock_item": 1},
		or_filters={"name": ["like", like], "item_name": ["like", like]},
		fields=["name", "item_name", "stock_uom", "custom_is_trailer"], order_by="item_name", limit_page_length=cint(limit) or 15,
	)
	if price_list and items:
		prices = {p.item_code: p.price_list_rate for p in frappe.get_all(
			"Item Price", filters={"price_list": price_list, "item_code": ["in", [i.name for i in items]]},
			fields=["item_code", "price_list_rate"])}
		for i in items:
			i["rate"] = prices.get(i.name, 0)
	return items


@frappe.whitelist()
def search_accounts(search=None, limit=15):
	"""Ledger accounts a journal line may post to."""
	frappe.has_permission("Account", "read", throw=True)
	like = "%{0}%".format((search or "").strip())
	filters = {"is_group": 0, "disabled": 0}
	company = default_company()
	if company:
		filters["company"] = company
	return frappe.get_all("Account", filters=filters, or_filters={"name": ["like", like], "account_name": ["like", like]},
	                      fields=["name", "account_name", "root_type"], order_by="name", limit_page_length=cint(limit) or 15)


@frappe.whitelist()
def search_party(doctype, search=None, limit=15):
	"""Customers or suppliers for a header picker."""
	if doctype not in ("Customer", "Supplier"):
		frappe.throw(_("Not a party"))
	frappe.has_permission(doctype, "read", throw=True)
	label = "customer_name" if doctype == "Customer" else "supplier_name"
	like = "%{0}%".format((search or "").strip())
	return frappe.get_all(doctype, filters={"disabled": 0}, or_filters={"name": ["like", like], label: ["like", like]},
	                      fields=["name", f"{label} as label"], order_by=label, limit_page_length=cint(limit) or 15)


@frappe.whitelist()
def list_warehouses():
	frappe.has_permission("Warehouse", "read", throw=True)
	filters = {"is_group": 0, "disabled": 0}
	company = default_company()
	if company:
		filters["company"] = company
	return frappe.get_all("Warehouse", filters=filters, fields=["name", "warehouse_name"], order_by="warehouse_name")


@frappe.whitelist()
def price_list(kind="buying"):
	field = "buying_price_list" if kind == "buying" else "selling_price_list"
	settings = "Buying Settings" if kind == "buying" else "Selling Settings"
	return (frappe.db.get_single_value(settings, field)
	        or frappe.db.get_value("Price List", {kind: 1, "enabled": 1}, "name"))
