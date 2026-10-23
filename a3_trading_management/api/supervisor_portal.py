# Implements: the Supervisor's own portal screens (overtime, staff requests,
# quotations, stock moves).
"""Four screens the floor needs that the rest of the portal did not have.

Unlike the older portal APIs these go through the normal permission checks (no
ignore_permissions), so each one does exactly what the user may do on the Desk:
the Supervisor raises and submits, a manager reverses. Nothing here returns a
price; the Supervisor's answers are also scrubbed (integrations.supervisor).
"""

import json

import frappe
from frappe import _
from frappe.utils import add_days, cint, flt, nowdate

from a3_trading_management.api._common import default_company, rows


def _lines(value):
	return value if isinstance(value, list) else json.loads(value or "[]")


# ------------------------------------------------------------------ overtime


@frappe.whitelist()
def list_overtime(limit=100):
	return rows("Employee Overtime",
	            ["name", "employee", "employee_name", "overtime_date", "from_time", "to_time", "hours",
	             "work_order", "reason", "docstatus"],
	            limit=limit, order_by="overtime_date desc, modified desc")


@frappe.whitelist()
def search_employees(search=None):
	"""Active employees for the overtime picker: id and name only. The Supervisor may
	pick an employee but not open the record (salary, bank and personal details)."""
	if not (frappe.has_permission("Employee", "select") or frappe.has_permission("Employee", "read")):
		frappe.throw(_("Not permitted"), frappe.PermissionError)
	like = "%{0}%".format((search or "").strip())
	return frappe.get_all("Employee", filters={"status": "Active"},
	                      or_filters={"name": ["like", like], "employee_name": ["like", like]},
	                      fields=["name", "employee_name", "designation"], order_by="employee_name",
	                      limit_page_length=20)


@frappe.whitelist()
def create_overtime(employee, reason, overtime_date=None, from_time=None, to_time=None, hours=None,
                    work_order=None, submit=1):
	doc = frappe.get_doc({
		"doctype": "Employee Overtime", "employee": employee, "reason": reason,
		"overtime_date": overtime_date or nowdate(), "from_time": from_time or None,
		"to_time": to_time or None, "hours": flt(hours), "work_order": work_order or None,
	})
	doc.insert()
	if cint(submit):
		doc.submit()
	return {"name": doc.name, "hours": doc.hours, "docstatus": doc.docstatus}


@frappe.whitelist()
def submit_overtime(name):
	doc = frappe.get_doc("Employee Overtime", name)
	doc.submit()
	return {"name": doc.name}


# ------------------------------------------------------------------ staff requests


@frappe.whitelist()
def list_staff_requests(limit=100):
	return rows("Job Requisition",
	            ["name", "designation", "no_of_positions", "status", "requested_by_name", "posting_date",
	             "expected_by", "reason_for_requesting"],
	            limit=limit, order_by="posting_date desc, modified desc")


@frappe.whitelist()
def list_designations():
	return frappe.get_all("Designation", pluck="name", order_by="name")


@frappe.whitelist()
def create_staff_request(designation, no_of_positions, description=None, reason=None, expected_by=None):
	"""Raised as Pending; HR approves it and sets the pay (integrations.supervisor)."""
	doc = frappe.get_doc({
		"doctype": "Job Requisition", "designation": designation, "no_of_positions": cint(no_of_positions),
		"description": description or designation, "reason_for_requesting": reason,
		"expected_by": expected_by or None, "posting_date": nowdate(), "status": "Pending",
		"company": default_company(),
	})
	doc.insert()
	return {"name": doc.name}


# ------------------------------------------------------------------ quotations


@frappe.whitelist()
def list_quotations(limit=100):
	quotes = rows("Quotation", ["name", "party_name", "customer_name", "transaction_date", "valid_till",
	                            "status", "docstatus", "owner"],
	              filters={"quotation_to": "Customer"}, limit=limit, order_by="transaction_date desc, modified desc")
	items = {}
	for i in frappe.get_all("Quotation Item", filters={"parent": ["in", [q.name for q in quotes] or [""]]},
	                        fields=["parent", "item_code", "item_name", "qty"], order_by="idx"):
		items.setdefault(i.parent, []).append({"item_code": i.item_code, "item_name": i.item_name, "qty": i.qty})
	for q in quotes:
		q["items"] = items.get(q.name, [])
	return quotes


@frappe.whitelist()
def create_quotation(customer, items, valid_till=None, notes=None):
	"""A draft without prices: Sales adds the rates and submits it."""
	lines = [l for l in _lines(items) if (l.get("item_code") or "").strip() and flt(l.get("qty")) > 0]
	if not lines:
		frappe.throw(_("Add at least one item with a quantity."))
	doc = frappe.get_doc({
		"doctype": "Quotation", "quotation_to": "Customer", "party_name": customer,
		"company": default_company(), "transaction_date": nowdate(),
		"valid_till": valid_till or add_days(nowdate(), 30),
		"items": [{"item_code": l["item_code"], "qty": flt(l["qty"])} for l in lines],
	})
	if notes:
		doc.terms = notes
	doc.insert()
	return {"name": doc.name}


# ------------------------------------------------------------------ stock moves

PURPOSES = ("Material Transfer", "Material Issue")


@frappe.whitelist()
def list_stock_moves(limit=100):
	entries = rows("Stock Entry", ["name", "stock_entry_type", "purpose", "posting_date", "work_order", "docstatus",
	                               "from_warehouse", "to_warehouse", "owner"],
	               limit=limit, order_by="posting_date desc, modified desc")
	lines = {}
	for d in frappe.get_all("Stock Entry Detail", filters={"parent": ["in", [e.name for e in entries] or [""]]},
	                        fields=["parent", "item_code", "qty", "uom", "s_warehouse", "t_warehouse"], order_by="idx"):
		lines.setdefault(d.parent, []).append(d)
	for e in entries:
		e["items"] = lines.get(e.name, [])
	return entries


@frappe.whitelist()
def create_stock_move(purpose, items, from_warehouse, to_warehouse=None, remarks=None, submit=1):
	"""Move stock between warehouses, or issue it out of one. Values are worked out
	by ERPNext from the valuation; nothing is priced here."""
	if purpose not in PURPOSES:
		frappe.throw(_("Choose Transfer or Issue."))
	if purpose == "Material Transfer" and not to_warehouse:
		frappe.throw(_("Choose the warehouse the stock goes to."))
	if purpose == "Material Transfer" and to_warehouse == from_warehouse:
		frappe.throw(_("From and To are the same warehouse."))
	lines = [l for l in _lines(items) if (l.get("item_code") or "").strip() and flt(l.get("qty")) > 0]
	if not lines:
		frappe.throw(_("Add at least one item with a quantity."))
	doc = frappe.new_doc("Stock Entry")
	doc.stock_entry_type = purpose
	doc.purpose = purpose
	doc.company = default_company()
	doc.from_warehouse = from_warehouse
	doc.to_warehouse = to_warehouse if purpose == "Material Transfer" else None
	doc.remarks = remarks
	for l in lines:
		doc.append("items", {
			"item_code": l["item_code"], "qty": flt(l["qty"]), "s_warehouse": from_warehouse,
			"t_warehouse": to_warehouse if purpose == "Material Transfer" else None,
		})
	doc.insert()
	if cint(submit):
		doc.submit()
	return {"name": doc.name, "docstatus": doc.docstatus}


@frappe.whitelist()
def submit_stock_move(name):
	doc = frappe.get_doc("Stock Entry", name)
	doc.submit()
	return {"name": doc.name}


# ------------------------------------------------------------------ BOMs


@frappe.whitelist()
def list_boms(limit=200):
	return rows("BOM", ["name", "item", "item_name", "quantity", "is_active", "is_default", "docstatus", "owner",
	                    "modified"], filters={"docstatus": ["<", 2]}, limit=limit, order_by="modified desc")


@frappe.whitelist()
def get_bom(name):
	"""Materials and operations of a BOM, quantities and times only."""
	bom = frappe.get_doc("BOM", name)
	bom.check_permission("read")
	return {
		"name": bom.name, "item": bom.item, "item_name": bom.item_name, "quantity": bom.quantity,
		"docstatus": bom.docstatus, "is_default": bom.is_default,
		"items": [{"item_code": i.item_code, "item_name": i.item_name, "qty": i.qty, "uom": i.uom} for i in bom.items],
		"operations": [{"operation": o.operation, "workstation": o.workstation, "hours": flt(o.time_in_mins) / 60}
		               for o in bom.operations],
	}


@frappe.whitelist()
def search_bom_items(search=None, trailers=0):
	"""Items for a BOM: what it builds (trailers=1) or what goes in it."""
	frappe.has_permission("Item", "read", throw=True)
	like = "%{0}%".format((search or "").strip())
	filters = {"disabled": 0, "has_variants": 0}
	if cint(trailers):
		filters["is_stock_item"] = 1
	return frappe.get_all("Item", filters=filters, or_filters={"name": ["like", like], "item_name": ["like", like]},
	                      fields=["name", "item_name", "stock_uom"], order_by="item_name", limit_page_length=20)


@frappe.whitelist()
def list_operations():
	return frappe.get_all("Operation", fields=["name", "workstation"], order_by="name")


# ------------------------------------------------------------------ items, customers, warehouses (view only)


@frappe.whitelist()
def list_items(search=None, limit=300):
	like = "%{0}%".format((search or "").strip())
	items = rows("Item", ["name", "item_name", "item_group", "stock_uom", "is_stock_item", "disabled"],
	             filters={"has_variants": 0}, limit=limit, order_by="item_name asc")
	if search:
		s = search.lower()
		items = [i for i in items if s in (i.name or "").lower() or s in (i.item_name or "").lower()]
	qty = {}
	for b in frappe.get_all("Bin", fields=["item_code", "actual_qty", "projected_qty", "reserved_qty", "ordered_qty"]):
		q = qty.setdefault(b.item_code, {"actual": 0, "projected": 0, "reserved": 0, "ordered": 0})
		q["actual"] += flt(b.actual_qty); q["projected"] += flt(b.projected_qty)
		q["reserved"] += flt(b.reserved_qty); q["ordered"] += flt(b.ordered_qty)
	for i in items:
		i.update(qty.get(i.name, {"actual": 0, "projected": 0, "reserved": 0, "ordered": 0}))
	return items


@frappe.whitelist()
def list_customers(limit=500):
	return rows("Customer", ["name", "customer_name", "customer_group", "territory", "mobile_no", "email_id", "disabled"],
	            limit=limit, order_by="customer_name asc")


@frappe.whitelist()
def list_warehouse_stock():
	frappe.has_permission("Warehouse", "read", throw=True)
	whs = frappe.get_all("Warehouse", filters={"is_group": 0, "disabled": 0, "company": default_company()},
	                     fields=["name", "warehouse_name", "parent_warehouse"], order_by="warehouse_name")
	agg = {}
	for b in frappe.get_all("Bin", fields=["warehouse", "item_code", "actual_qty", "projected_qty"]):
		a = agg.setdefault(b.warehouse, {"items": 0, "qty": 0, "projected": 0})
		if flt(b.actual_qty) > 0:
			a["items"] += 1
		a["qty"] += flt(b.actual_qty); a["projected"] += flt(b.projected_qty)
	for w in whs:
		w.update(agg.get(w.name, {"items": 0, "qty": 0, "projected": 0}))
	return whs


@frappe.whitelist()
def projected_stock(warehouse=None):
	"""Stock Projected Qty, the portal way: quantities per item and warehouse, no values."""
	frappe.has_permission("Bin", "read", throw=True)
	filters = {"warehouse": warehouse} if warehouse else {}
	rows_ = frappe.get_all("Bin", filters=filters,
	                       fields=["item_code", "warehouse", "actual_qty", "reserved_qty", "ordered_qty",
	                               "indented_qty", "planned_qty", "reserved_qty_for_production", "projected_qty"],
	                       order_by="item_code")
	names = dict(frappe.get_all("Item", fields=["name", "item_name"], as_list=True))
	reorder = {(r.parent, r.warehouse): r.warehouse_reorder_level for r in frappe.get_all(
		"Item Reorder", fields=["parent", "warehouse", "warehouse_reorder_level"])}
	for r in rows_:
		r["item_name"] = names.get(r.item_code, r.item_code)
		r["reorder_level"] = reorder.get((r.item_code, r.warehouse))
	return rows_


# ------------------------------------------------------------------ the Supervisor's home


def home_counters():
	"""The Desk workspace's counters and a few more, for the portal home."""
	from frappe.utils import get_first_day

	month_start = get_first_day(nowdate())
	return [
		{"label": "Open Work Orders", "value": frappe.db.count("Work Order", {"docstatus": 1, "status": ["in", ["Not Started", "In Process"]]}),
		 "sub": "Not started or in process", "route": "/a3-workshop/manufacturing"},
		{"label": "Pending Material Requests", "value": frappe.db.count("Material Request", {"docstatus": 1, "status": ["in", ["Pending", "Partially Ordered"]]}),
		 "sub": "Waiting for Purchase", "route": "/a3-workshop/purchase-requisitions"},
		{"label": "Overtime This Month", "value": "{:.2f} h".format(flt(frappe.db.sql(
			"select coalesce(sum(hours), 0) from `tabEmployee Overtime` where docstatus = 1 and overtime_date >= %s",
			month_start)[0][0])), "sub": "Submitted hours", "route": "/a3-workshop/overtime"},
		{"label": "Staff Requests Pending", "value": frappe.db.count("Job Requisition", {"status": "Pending"}),
		 "sub": "Waiting for HR", "route": "/a3-workshop/staff-requests"},
		{"label": "Draft BOMs", "value": frappe.db.count("BOM", {"docstatus": 0}), "sub": "Waiting for the manager",
		 "route": "/a3-workshop/boms"},
		{"label": "Draft Quotations", "value": frappe.db.count("Quotation", {"docstatus": 0}), "sub": "Waiting for Sales",
		 "route": "/a3-workshop/quotations"},
	]
