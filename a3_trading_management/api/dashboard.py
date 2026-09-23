# Implements: the portal home -- the trading picture in one payload.
"""What the owner wants to see before opening anything: trailers in stock and on
the floor, this month's deliveries and revenue, money owed each way, the open
work orders, the latest deliveries and what the store is short of."""

import frappe
from frappe import _
from frappe.utils import flt, fmt_money, formatdate, get_first_day, nowdate

STAGES = ["Material", "Fabrication", "Assembly", "Paint", "QC", "Delivery"]


def _company():
	return frappe.defaults.get_user_default("Company") or frappe.db.get_default("company")


def _currency(company):
	return (frappe.get_cached_value("Company", company, "default_currency") if company else None) \
		or frappe.defaults.get_global_default("currency")


@frappe.whitelist()
def get_dashboard():
	if frappe.session.user == "Guest":
		frappe.throw(_("You must be logged in."), frappe.PermissionError)
	return {
		"tiles": get_stat_tiles(),
		"stages": get_trailers_by_stage(),
		"work_orders": get_open_work_orders(),
		"deliveries": get_recent_deliveries(),
		"low_stock": get_low_stock(),
	}


def get_stat_tiles():
	company = _company()
	currency = _currency(company)
	month_start = get_first_day(nowdate())
	scope = {"company": company} if company else {}

	in_stock = frappe.db.count("Trailer Serial", {"status": "In Stock"})
	in_production = frappe.db.count("Trailer Serial", {"status": "In Production"})
	delivered = frappe.db.count(
		"Delivery Note", dict(scope, docstatus=1, posting_date=[">=", month_start],
		                      **({"custom_trailer_serial": ["is", "set"]} if frappe.get_meta("Delivery Note").has_field("custom_trailer_serial") else {})),
	)
	revenue = frappe.db.get_value(
		"Sales Invoice", dict(scope, docstatus=1, posting_date=[">=", month_start]), "sum(grand_total)",
	) or 0
	owed_to_us = frappe.db.get_value("Sales Invoice", dict(scope, docstatus=1), "sum(outstanding_amount)") or 0
	owed_by_us = frappe.db.get_value("Purchase Invoice", dict(scope, docstatus=1), "sum(outstanding_amount)") or 0
	return [
		{"label": "Trailers in Stock", "value": "{:,}".format(in_stock), "sub": "Ready to sell"},
		{"label": "In Production", "value": "{:,}".format(in_production), "sub": "On the floor"},
		{"label": "Delivered", "value": "{:,}".format(delivered), "sub": formatdate(month_start, "MMMM yyyy")},
		{"label": "Revenue", "value": fmt_money(revenue, currency=currency), "sub": formatdate(month_start, "MMMM yyyy")},
		{"label": "Owed to Us", "value": fmt_money(owed_to_us, currency=currency), "sub": "Customer invoices open"},
		{"label": "Owed by Us", "value": fmt_money(owed_by_us, currency=currency), "sub": "Supplier bills open"},
	]


def get_trailers_by_stage():
	"""Where the trailers on the floor are, stage by stage, plus the three states
	after the floor."""
	counts = dict(frappe.db.sql(
		"select production_stage, count(*) from `tabTrailer Serial` where status = 'In Production' group by production_stage"
	))
	top = max(list(counts.values()) or [0]) or 1
	stages = [{"stage": s, "count": int(counts.get(s, 0)), "pct": int(round(100 * counts.get(s, 0) / top))} for s in STAGES]
	after = {s: frappe.db.count("Trailer Serial", {"status": s}) for s in ("In Stock", "Sold", "Delivered")}
	return {"stages": stages, "on_floor": sum(counts.values()), "after": after}


def get_open_work_orders(limit=6):
	"""Work orders still to build: drafts and submitted ones not yet completed,
	with ERPNext's own status alongside the production stage."""
	from a3_trading_management.api.manufacturing import _progress, _wo_rows
	out = []
	for r in _wo_rows():
		if r.docstatus == 2 or (r.status or "") in ("Completed", "Stopped", "Closed", "Cancelled"):
			continue
		stage = r.custom_production_stage or "Material"
		status = "Draft" if r.docstatus == 0 else (r.status or "Not Started")
		out.append({
			"name": r.name,
			"item_name": r.item_name or r.production_item,
			"qty": r.qty,
			"produced_qty": r.produced_qty,
			"stage": stage,
			"status": status,
			"badge": {"Draft": "badge--soft", "Not Started": "badge--pending", "In Process": "badge--progress"}.get(status, "badge--navy"),
			"progress": _progress(stage, r.docstatus),
		})
		if len(out) >= limit:
			break
	return out


def get_recent_deliveries(limit=6):
	has_chassis = frappe.get_meta("Delivery Note").has_field("custom_chassis_number")
	fields = ["name", "customer_name", "customer", "posting_date", "grand_total", "currency"] + (["custom_chassis_number"] if has_chassis else [])
	out = []
	for d in frappe.get_all("Delivery Note", filters={"docstatus": 1}, fields=fields, order_by="posting_date desc, creation desc", limit=limit):
		out.append({
			"name": d.name,
			"customer": d.customer_name or d.customer,
			"date": formatdate(d.posting_date, "dd MMM yyyy"),
			"chassis": d.get("custom_chassis_number") or "—",
			"total": fmt_money(flt(d.grand_total), currency=d.currency),
		})
	return out


def get_low_stock(limit=6):
	from a3_trading_management.api.live_stock import get_low_stock_alerts
	return (get_low_stock_alerts() or [])[:limit]
