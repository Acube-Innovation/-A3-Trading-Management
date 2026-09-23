"""Printable Work Order traveler for the fabrication floor (?workorder=<name>):
stages, materials, operations and the stage history."""

import frappe
from frappe.utils import flt, formatdate, format_datetime, now_datetime

from a3_trading_management.website_utils import require_login

no_cache = 1


def _fmt_date(v):
	return formatdate(v, "dd MMM yyyy") if v else "—"


def _fmt_dt(v):
	return format_datetime(v, "dd MMM yyyy HH:mm") if v else "—"


def get_context(context):
	require_login(context)
	fd = frappe.form_dict
	context.printed_on = now_datetime().strftime("%d %b %Y %H:%M")
	context.printed_by = frappe.db.get_value("User", frappe.session.user, "full_name") or frappe.session.user
	if fd.get("workorder"):
		_workorder(context, fd.get("workorder").strip())
	else:
		context.mode = "none"
		context.title = "Print"
	return context


def _workorder(context, name):
	if not frappe.db.exists("Work Order", name):
		context.mode = "none"
		context.title = "Print"
		return
	doc = frappe.get_doc("Work Order", name)
	context.mode = "workorder"
	context.title = f"Traveler {name}"
	context.wo = {
		"name": doc.name,
		"item": doc.item_name or doc.production_item,
		"qty": flt(doc.qty),
		"status": doc.status,
		"stage": doc.get("custom_production_stage") or "—",
		"bom": doc.bom_no or "—",
		"planned_start": _fmt_date(doc.planned_start_date),
		"delivery": _fmt_date(doc.get("expected_delivery_date")),
		"source": doc.source_warehouse or doc.wip_warehouse or "—",
		"fg": doc.fg_warehouse or "—",
		"spec": doc.description or "",

		"materials": [
			{"item": r.item_name or r.item_code, "qty": flt(r.required_qty),
			 "warehouse": r.source_warehouse or "—"}
			for r in (doc.get("required_items") or [])
		],
		"operations": [
			{"operation": o.operation, "workstation": o.workstation or "—",
			 "mins": flt(o.time_in_mins), "status": o.get("status") or "—"}
			for o in (doc.get("operations") or [])
		],
		"history": [
			{"stage": h.stage, "from_stage": h.from_stage or "—",
			 "entered_on": _fmt_dt(h.entered_on), "entered_by": h.entered_by or "—",
			 "hours": flt(h.duration_hours), "notes": h.notes or ""}
			for h in (doc.get("custom_stage_history") or [])
		],
	}
