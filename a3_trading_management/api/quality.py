# Implements: the Quality screen — what is waiting for inspection, every inspection
# recorded, and the checklists they are recorded against.
"""Quality control over ERPNext's own Quality Inspection.

Three things wait for an inspection:

* a work order at its QC production stage (in-process: the finished trailer
  before it goes to stock) — referenced directly, see
  integrations.quality_inspection;
* a draft goods receipt line whose item is ticked "inspection required before
  purchase" (incoming);
* a draft delivery note line whose item is ticked "inspection required before
  delivery" (outgoing).

A checklist is a Quality Inspection Template: its parameters are the readings an
inspector records, and an item carries the template it is inspected against.

A work order moved past QC on the Manufacturing screen without an inspection gets
one recorded as Accepted automatically (`ensure_passed_before_delivery`), so it
still appears in the list. A work order whose last inspection was Rejected cannot
be moved past QC until it passes.
"""

import json

import frappe
from frappe import _
from frappe.utils import cint, flt, formatdate, getdate, nowdate

QI = "Quality Inspection"
WO = "Work Order"
PR = "Purchase Receipt"
DN = "Delivery Note"

INSPECTION_TYPE = {WO: "In Process", PR: "Incoming", DN: "Outgoing"}
SOURCE_LABEL = {WO: "Manufacturing", PR: "Goods receipt", DN: "Delivery"}


def _parse(value):
	if isinstance(value, str):
		return json.loads(value or "null")
	return value


def _date(value):
	return formatdate(value, "dd MMM yyyy") if value else ""


def _user_names(users):
	users = {u for u in users if u}
	if not users:
		return {}
	return {
		r.name: r.full_name or r.name
		for r in frappe.get_all("User", filters={"name": ["in", list(users)]}, fields=["name", "full_name"])
	}


def _chassis_by_work_order(work_orders):
	out = {}
	if not work_orders:
		return out
	for r in frappe.get_all(
		"Trailer Serial",
		filters={"work_order": ["in", list(work_orders)]},
		fields=["work_order", "chassis_number"],
		order_by="chassis_number asc",
	):
		if r.chassis_number:
			out.setdefault(r.work_order, []).append(r.chassis_number)
	return out


def latest_inspection(reference_type, reference_name, item_code=None):
	"""The most recent submitted inspection against a document, or None."""
	filters = {"reference_type": reference_type, "reference_name": reference_name, "docstatus": 1}
	if item_code:
		filters["item_code"] = item_code
	rows = frappe.get_all(
		QI, filters=filters, fields=["name", "status", "report_date"],
		order_by="creation desc", limit_page_length=1,
	)
	return rows[0] if rows else None


# ---------------------------------------------------------------------------
# Waiting for inspection
# ---------------------------------------------------------------------------


def _pending_work_orders():
	rows = frappe.db.sql(
		"""
		select wo.name, wo.production_item as item_code, wo.item_name, wo.qty, wo.sales_order,
		       wo.expected_delivery_date,
		       (select max(l.entered_on) from `tabWorkshop Work Order Stage Log` l
		         where l.parent = wo.name and l.parenttype = 'Work Order' and l.stage = 'QC') as since
		from `tabWork Order` wo
		where wo.docstatus = 0 and wo.custom_production_stage = 'QC'
		order by since asc, wo.name asc
		""",
		as_dict=True,
	)
	chassis = _chassis_by_work_order([r.name for r in rows])
	customers = {}
	orders = [r.sales_order for r in rows if r.sales_order]
	if orders:
		customers = dict(frappe.get_all(
			"Sales Order", filters={"name": ["in", orders]}, fields=["name", "customer_name"], as_list=True
		))
	out = []
	for r in rows:
		last = latest_inspection(WO, r.name)
		if last and last.status == "Accepted":
			continue  # passed; waits only for the Manufacturing screen to deliver it
		party = customers.get(r.sales_order) or ""
		out.append({
			"reference_type": WO,
			"reference_name": r.name,
			"child_row": "",
			"item_code": r.item_code,
			"item_name": r.item_name or r.item_code,
			"qty": flt(r.qty),
			"party": party or "Build to stock",
			"detail": ", ".join(chassis.get(r.name) or []),
			"since": _date(r.since),
			"since_raw": str(r.since or ""),
			"due": _date(r.expected_delivery_date),
			"state": "Re-inspect" if last else "To inspect",
			"last_inspection": last.name if last else "",
		})
	return out


def _pending_lines(parent_doctype, flag, party_field, qty_field):
	child = parent_doctype + " Item"
	rows = frappe.db.sql(
		f"""
		select p.name, p.{party_field} as party, p.posting_date, c.name as child_row,
		       c.item_code, c.item_name, c.{qty_field} as qty, qi.name as failed_by
		from `tab{child}` c
		join `tab{parent_doctype}` p on p.name = c.parent
		join `tabItem` i on i.name = c.item_code
		left join `tabQuality Inspection` qi
		       on qi.name = c.quality_inspection and qi.docstatus = 1 and qi.status = 'Rejected'
		where p.docstatus = 0 and i.{flag} = 1
		  and (ifnull(c.quality_inspection, '') = '' or qi.name is not null)
		order by p.posting_date asc, p.name asc, c.idx asc
		""",
		as_dict=True,
	)
	return [
		{
			"reference_type": parent_doctype,
			"reference_name": r.name,
			"child_row": r.child_row,
			"item_code": r.item_code,
			"item_name": r.item_name or r.item_code,
			"qty": flt(r.qty),
			"party": r.party or "",
			"detail": "",
			"since": _date(r.posting_date),
			"since_raw": str(r.posting_date or ""),
			"due": "",
			# A rejected lot stays on the list: ERPNext will not post the receipt
			# or delivery while its line carries a rejected inspection.
			"state": "Re-inspect" if r.failed_by else "To inspect",
			"last_inspection": r.failed_by or "",
		}
		for r in rows
	]


def pending():
	out = _pending_work_orders()
	out += _pending_lines(PR, "inspection_required_before_purchase", "supplier_name", "received_qty")
	out += _pending_lines(DN, "inspection_required_before_delivery", "customer_name", "qty")
	for r in out:
		r["source"] = SOURCE_LABEL[r["reference_type"]]
	return out


# ---------------------------------------------------------------------------
# Inspections recorded
# ---------------------------------------------------------------------------


def inspections(limit=500):
	rows = frappe.get_all(
		QI,
		filters={"docstatus": ["<", 2]},
		fields=[
			"name", "report_date", "inspection_type", "reference_type", "reference_name",
			"item_code", "item_name", "sample_size", "status", "inspected_by", "remarks",
			"docstatus", "quality_inspection_template", "custom_auto_recorded",
		],
		order_by="report_date desc, creation desc",
		limit_page_length=cint(limit) or 500,
	)
	names = _user_names(r.inspected_by for r in rows)
	chassis = _chassis_by_work_order([r.reference_name for r in rows if r.reference_type == WO])
	for r in rows:
		r["date"] = _date(r.report_date)
		r["inspector"] = names.get(r.inspected_by, r.inspected_by or "")
		r["source"] = SOURCE_LABEL.get(r.reference_type, r.reference_type)
		r["detail"] = ", ".join(chassis.get(r.reference_name) or []) if r.reference_type == WO else ""
		r["state"] = "Draft" if r.docstatus == 0 else r.status
	return rows


def templates():
	out = []
	has_default = frappe.get_meta(QI + " Template").has_field("custom_is_default")
	fields = ["name", "custom_is_default"] if has_default else ["name"]
	for t in frappe.get_all(QI + " Template", fields=fields, order_by="name asc"):
		params = frappe.get_all(
			"Item Quality Inspection Parameter",
			filters={"parenttype": QI + " Template", "parent": t.name},
			fields=["specification", "numeric", "min_value", "max_value", "value"],
			order_by="idx asc",
		)
		items = frappe.get_all(
			"Item", filters={"quality_inspection_template": t.name},
			fields=["name", "item_name", "inspection_required_before_purchase", "inspection_required_before_delivery"],
			order_by="item_name asc",
		)
		out.append({"name": t.name, "parameters": params, "used_by": items,
		            "is_default": cint(t.get("custom_is_default"))})
	return out


@frappe.whitelist()
def get_board():
	frappe.has_permission(QI, "read", throw=True)
	waiting = pending()
	done = inspections()
	month_start = getdate(nowdate()).replace(day=1)
	this_month = [r for r in done if r.docstatus == 1 and r.report_date and getdate(r.report_date) >= month_start]
	accepted = len([r for r in this_month if r.status == "Accepted"])
	return {
		"pending": waiting,
		"inspections": done,
		"templates": templates(),
		"stats": {
			"waiting": len(waiting),
			"reinspect": len([r for r in waiting if r["state"] == "Re-inspect"]),
			"month": len(this_month),
			"accepted": accepted,
			"rejected": len(this_month) - accepted,
			"pass_rate": int(round(accepted * 100.0 / len(this_month))) if this_month else None,
		},
	}


# ---------------------------------------------------------------------------
# Record an inspection
# ---------------------------------------------------------------------------


def default_template():
	"""The one checklist marked default, used for any item with none of its own."""
	if not frappe.get_meta(QI + " Template").has_field("custom_is_default"):
		return ""
	return frappe.db.get_value(QI + " Template", {"custom_is_default": 1}, "name") or ""


def _template_for(reference_type, reference_name, item_code):
	"""The item's own checklist, else its BOM's, else the default checklist — so
	a new item with no checklist is still inspected against one."""
	template = frappe.db.get_value("Item", item_code, "quality_inspection_template")
	if not template and reference_type == WO:
		bom = frappe.db.get_value(WO, reference_name, "bom_no")
		if bom:
			template = frappe.db.get_value("BOM", bom, "quality_inspection_template")
	return template or default_template()


def _parameters(template):
	from erpnext.stock.doctype.quality_inspection_template.quality_inspection_template import (
		get_template_details,
	)

	return [
		{
			"specification": p.specification,
			"numeric": cint(p.numeric),
			"min_value": flt(p.min_value),
			"max_value": flt(p.max_value),
			"value": p.value or "",
		}
		for p in get_template_details(template)
	]


@frappe.whitelist()
def get_template_parameters(template):
	frappe.has_permission(QI, "read", throw=True)
	return _parameters(template)


def _last_inspection_detail(reference_type, reference_name, item_code):
	"""The previous inspection, with what it found per check, so a re-check can
	show what failed last time."""
	last = latest_inspection(reference_type, reference_name, item_code)
	if not last:
		return None, {}
	qi = frappe.get_doc(QI, last.name)
	previous = {}
	failed = []
	for r in qi.readings:
		reading = (r.reading_1 if cint(r.numeric) else r.reading_value) or ""
		previous[r.specification] = {"reading": reading, "status": r.status}
		if r.status == "Rejected":
			failed.append({"specification": r.specification, "reading": reading})
	return {
		"name": qi.name,
		"status": qi.status,
		"date": _date(qi.report_date),
		"remarks": qi.remarks or "",
		"inspector": _user_names([qi.inspected_by]).get(qi.inspected_by, qi.inspected_by),
		"failed": failed,
	}, previous


@frappe.whitelist()
def get_inspection_form(reference_type, reference_name, item_code=None, child_row=None):
	"""What the inspect pop-up needs: the thing being inspected, what happened at
	its last inspection, and the checklist."""
	frappe.has_permission(QI, "create", throw=True)
	if reference_type not in INSPECTION_TYPE:
		frappe.throw(_("Cannot inspect a {0}").format(reference_type))
	doc = frappe.get_doc(reference_type, reference_name)
	chassis, since, due = [], "", ""
	if reference_type == WO:
		item_code = doc.production_item
		item_name = doc.item_name or doc.production_item
		qty, uom = flt(doc.qty), doc.stock_uom or ""
		chassis = _chassis_by_work_order([doc.name]).get(doc.name) or []
		party_label = "For"
		party = (frappe.db.get_value("Sales Order", doc.sales_order, "customer_name") if doc.sales_order else "") \
			or _("Build to stock")
		since = frappe.db.sql(
			"""select max(entered_on) from `tabWorkshop Work Order Stage Log`
			   where parent = %s and parenttype = 'Work Order' and stage = 'QC'""", doc.name)[0][0]
		due = doc.expected_delivery_date
	else:
		row = next((r for r in doc.items if r.name == child_row), None) or next(
			(r for r in doc.items if r.item_code == item_code), None
		)
		if not row:
			frappe.throw(_("{0} is not on {1}").format(item_code, reference_name))
		item_code, child_row = row.item_code, row.name
		item_name = row.item_name or row.item_code
		qty, uom = flt(row.get("received_qty") or row.qty), row.uom or ""
		party_label = "Supplier" if reference_type == PR else "Customer"
		party = doc.get("supplier_name") or doc.get("customer_name") or ""
		since = doc.posting_date
	meta = [
		[reference_type, doc.name],
		["Quantity", "{0:g} {1}".format(qty, uom).strip()],
		[party_label, party or "—"],
		["Waiting since", _date(since) or "—"],
	]
	if due:
		meta.append(["Due", _date(due)])
	template = _template_for(reference_type, reference_name, item_code)
	own = frappe.db.get_value("Item", item_code, "quality_inspection_template")
	last, previous = _last_inspection_detail(reference_type, reference_name, item_code)
	return {
		"reference_type": reference_type,
		"reference_name": reference_name,
		"child_row": child_row or "",
		"item_code": item_code,
		"item_name": item_name,
		"kind": {WO: _("In-process QC"), PR: _("Incoming inspection"), DN: _("Outgoing inspection")}[reference_type],
		"sample_size": qty,
		"chassis": chassis,
		"meta": meta,
		"template": template,
		"template_is_default": bool(template and not own and template == default_template()),
		"parameters": _parameters(template),
		"templates": frappe.get_all(QI + " Template", pluck="name", order_by="name asc"),
		"last": last,
		"previous": previous,
		"can_deliver": reference_type == WO and doc.docstatus == 0
		and (doc.custom_production_stage or "") == "QC",
	}


def _make_inspection(reference_type, reference_name, item_code, readings, status=None,
                     remarks=None, template=None, sample_size=None, child_row=None, auto=False):
	"""Create and submit one Quality Inspection.

	`readings` are [{specification, reading, result}] with result Accepted or
	Rejected as the inspector decided it; each row is marked manual so ERPNext
	keeps that decision. With readings the overall status follows them (any
	rejected reading rejects the inspection); without, `status` is used."""
	qi = frappe.new_doc(QI)
	qi.update({
		"inspection_type": INSPECTION_TYPE[reference_type],
		"reference_type": reference_type,
		"reference_name": reference_name,
		"item_code": item_code,
		"report_date": nowdate(),
		"sample_size": flt(sample_size) or 1,
		"inspected_by": frappe.session.user,
		"remarks": remarks or "",
		"custom_auto_recorded": 1 if auto else 0,
	})
	if child_row:
		qi.child_row_reference = child_row
	if reference_type == WO:
		qi.bom_no = frappe.db.get_value(WO, reference_name, "bom_no")

	spec = {p["specification"]: p for p in _parameters(template)} if template else {}
	qi.quality_inspection_template = template or None
	for r in readings or []:
		# A check from the checklist keeps the checklist's limits; one the
		# inspector added in the pop-up brings its own.
		p = spec.get(r.get("specification")) or r
		_ensure_parameter(r.get("specification"))
		result = "Rejected" if (r.get("result") or "") == "Rejected" else "Accepted"
		row = {
			"specification": r.get("specification"),
			"status": result,
			"manual_inspection": 1,
			"numeric": cint(p.get("numeric")),
			"min_value": flt(p.get("min_value")),
			"max_value": flt(p.get("max_value")),
			"value": p.get("value") or "",
		}
		reading = str(r.get("reading") or "").strip()
		if cint(p.get("numeric")):
			row["reading_1"] = reading
		else:
			row["reading_value"] = reading
		qi.append("readings", row)

	if not qi.readings:
		if status not in ("Accepted", "Rejected"):
			frappe.throw(_("Pass or fail the inspection"))
		qi.manual_inspection = 1
		qi.status = status
		if auto:
			# Moving past QC on the Manufacturing screen signs off the whole
			# checklist: record each check with that one verdict.
			qi.quality_inspection_template = _template_for(reference_type, reference_name, item_code) or None
			for p in _parameters(qi.quality_inspection_template):
				qi.append("readings", {
					"specification": p["specification"], "status": status, "manual_inspection": 1,
					"numeric": p["numeric"], "min_value": p["min_value"], "max_value": p["max_value"],
					"value": p["value"],
				})
		else:
			# No checklist chosen, or every check removed: one verdict, no readings.
			qi.quality_inspection_template = None
			qi.flags.a3_no_checklist = True

	qi.flags.ignore_permissions = True
	qi.insert(ignore_permissions=True)
	qi.submit()

	# ERPNext links the receipt / delivery line only when Stock Settings says
	# "Stop"; the line must carry it either way or it stays in the waiting list.
	if child_row and reference_type in (PR, DN):
		frappe.db.set_value(reference_type + " Item", child_row, "quality_inspection", qi.name, update_modified=False)
	return qi


@frappe.whitelist()
def record_inspection(reference_type, reference_name, item_code=None, readings=None, status=None,
                      remarks=None, template=None, sample_size=None, child_row=None, deliver=0):
	"""Record an inspection from the Quality screen. For a work order that passes,
	`deliver` also moves it to Delivery — submitting it and posting the trailers
	into stock, exactly as the Manufacturing screen's last step does."""
	frappe.has_permission(QI, "create", throw=True)
	if reference_type not in INSPECTION_TYPE:
		frappe.throw(_("Cannot inspect a {0}").format(reference_type))
	if reference_type == WO:
		item_code = frappe.db.get_value(WO, reference_name, "production_item")
	readings = _parse(readings) or []
	failing = status == "Rejected" if not readings else any(r.get("result") == "Rejected" for r in readings)
	if failing and not (remarks or "").strip():
		frappe.throw(_("Say what failed and what has to be fixed"))
	qi = _make_inspection(
		reference_type, reference_name, item_code, readings, status=status,
		remarks=remarks, template=template or None, sample_size=sample_size, child_row=child_row,
	)
	out = {"name": qi.name, "status": qi.status, "delivered": False}
	if reference_type == WO and qi.status == "Accepted" and cint(deliver):
		stage = frappe.db.get_value(WO, reference_name, "custom_production_stage")
		if stage == "QC":
			from a3_trading_management.api.manufacturing import advance_stage

			res = advance_stage(reference_name, notes=_("QC passed — {0}").format(qi.name))
			out.update({"delivered": True, "stock_entry": res.get("stock_entry"),
			            "stock_warning": res.get("stock_warning")})
	return out


def ensure_passed_before_delivery(wo, notes=None):
	"""Called as the Manufacturing screen moves a work order from QC to Delivery.

	Passed already: nothing to do. Last inspection rejected: refuse. None at all:
	moving it on IS the QC sign-off, so record that as an Accepted inspection."""
	last = latest_inspection(WO, wo.name)
	if last and last.status == "Accepted":
		return last.name
	if last:
		frappe.throw(
			_("{0} failed quality inspection {1}. Record a passing inspection on the Quality screen before delivering it.")
			.format(wo.name, last.name)
		)
	qi = _make_inspection(
		WO, wo.name, wo.production_item, [], status="Accepted",
		remarks=notes or _("QC passed on the Manufacturing screen."),
		sample_size=wo.qty, auto=True,
	)
	return qi.name


@frappe.whitelist()
def get_inspection(name):
	frappe.has_permission(QI, "read", throw=True)
	qi = frappe.get_doc(QI, name)
	chassis = _chassis_by_work_order([qi.reference_name]).get(qi.reference_name) if qi.reference_type == WO else None
	return {
		"name": qi.name,
		"status": qi.status,
		"docstatus": qi.docstatus,
		"date": _date(qi.report_date),
		"inspection_type": qi.inspection_type,
		"source": SOURCE_LABEL.get(qi.reference_type, qi.reference_type),
		"reference_type": qi.reference_type,
		"reference_name": qi.reference_name,
		"item": qi.item_name or qi.item_code,
		"sample_size": flt(qi.sample_size),
		"inspector": _user_names([qi.inspected_by]).get(qi.inspected_by, qi.inspected_by),
		"template": qi.quality_inspection_template or "",
		"remarks": qi.remarks or "",
		"auto": cint(qi.get("custom_auto_recorded")),
		"chassis": ", ".join(chassis or []),
		"readings": [
			{
				"specification": r.specification,
				"criteria": ("{0:g} – {1:g}".format(flt(r.min_value), flt(r.max_value)) if cint(r.numeric) else (r.value or "")),
				"reading": (r.reading_1 if cint(r.numeric) else r.reading_value) or "",
				"status": r.status,
			}
			for r in qi.readings
		],
	}


@frappe.whitelist()
def cancel_inspection(name):
	"""Withdraw an inspection recorded in error. The document it checked goes back
	to waiting for inspection."""
	frappe.has_permission(QI, "cancel", throw=True)
	qi = frappe.get_doc(QI, name)
	if qi.docstatus != 1:
		frappe.throw(_("Only a recorded inspection can be cancelled"))
	if qi.reference_type == WO and frappe.db.get_value(WO, qi.reference_name, "docstatus") == 1:
		frappe.throw(_("{0} is already delivered to stock; its inspection stands.").format(qi.reference_name))
	qi.flags.ignore_permissions = True
	qi.cancel()
	if qi.reference_type in (PR, DN):
		frappe.db.sql(
			f"update `tab{qi.reference_type} Item` set quality_inspection = '' where quality_inspection = %s",
			qi.name,
		)
	return qi.name


# ---------------------------------------------------------------------------
# Checklists (Quality Inspection Templates)
# ---------------------------------------------------------------------------


def _ensure_parameter(name):
	name = (name or "").strip()
	if name and not frappe.db.exists("Quality Inspection Parameter", name):
		frappe.get_doc({"doctype": "Quality Inspection Parameter", "parameter": name}).insert(ignore_permissions=True)
	return name


@frappe.whitelist()
def search_parameters(search=None, limit=10):
	"""Checks already defined, for the pop-up's "Add check" search. Each comes
	with the limits it was last given on a checklist, to prefill."""
	frappe.has_permission(QI, "create", throw=True)
	rows = frappe.get_all(
		"Quality Inspection Parameter",
		filters={"name": ["like", "%{0}%".format((search or "").strip())]},
		pluck="name", order_by="name asc", limit_page_length=cint(limit) or 10,
	)
	out = []
	for name in rows:
		used = frappe.get_all(
			"Item Quality Inspection Parameter",
			filters={"specification": name, "parenttype": QI + " Template"},
			fields=["numeric", "min_value", "max_value", "value"],
			order_by="modified desc", limit_page_length=1,
		)
		p = used[0] if used else {}
		out.append({
			"specification": name,
			"numeric": cint(p.get("numeric")),
			"min_value": flt(p.get("min_value")),
			"max_value": flt(p.get("max_value")),
			"value": p.get("value") or "",
		})
	return out


@frappe.whitelist()
def add_parameter(name):
	"""The pop-up's "Create" choice: a new check, available from then on."""
	frappe.has_permission(QI, "create", throw=True)
	name = _ensure_parameter(name)
	if not name:
		frappe.throw(_("Name the check"))
	return name


@frappe.whitelist()
def search_items(search=None, limit=15):
	frappe.has_permission("Item", "read", throw=True)
	txt = "%{0}%".format((search or "").strip())
	return frappe.db.sql(
		"""
		select name, item_name, quality_inspection_template
		from `tabItem`
		where disabled = 0 and is_stock_item = 1 and has_variants = 0
		  and (name like %(txt)s or item_name like %(txt)s)
		order by custom_is_trailer desc, item_name asc
		limit %(limit)s
		""",
		{"txt": txt, "limit": cint(limit) or 15},
		as_dict=True,
	)


@frappe.whitelist()
def save_template(template_name, parameters, items=None, on_receipt=0, before_delivery=0, name=None,
                  is_default=None):
	"""Create or update a checklist and the items inspected against it.

	`parameters`: [{specification, numeric, min_value, max_value, value}].
	`items`: item codes that use this checklist; items that used it and are left
	out stop using it. `on_receipt` / `before_delivery` set the item flags that
	put a goods receipt or delivery line on the waiting list."""
	frappe.has_permission(QI + " Template", "write" if name else "create", throw=True)
	template_name = (template_name or "").strip()
	if not template_name:
		frappe.throw(_("Name the checklist"))
	params = [p for p in (_parse(parameters) or []) if (p.get("specification") or "").strip()]
	if not params:
		frappe.throw(_("Add at least one check"))

	for p in params:
		spec = p["specification"].strip()
		p["specification"] = spec
		_ensure_parameter(spec)
		if cint(p.get("numeric")) and flt(p.get("min_value")) > flt(p.get("max_value")):
			frappe.throw(_("{0}: the minimum is above the maximum").format(spec))

	if name:
		doc = frappe.get_doc(QI + " Template", name)
	else:
		if frappe.db.exists(QI + " Template", template_name):
			frappe.throw(_("A checklist named {0} already exists").format(template_name))
		doc = frappe.new_doc(QI + " Template")
		doc.quality_inspection_template_name = template_name
	doc.set("item_quality_inspection_parameter", [])
	for p in params:
		numeric = cint(p.get("numeric"))
		doc.append("item_quality_inspection_parameter", {
			"specification": p["specification"],
			"numeric": numeric,
			"min_value": flt(p.get("min_value")) if numeric else 0,
			"max_value": flt(p.get("max_value")) if numeric else 0,
			"value": "" if numeric else (p.get("value") or "").strip(),
		})
	doc.flags.ignore_permissions = True
	doc.save(ignore_permissions=True)
	if is_default is not None:
		_set_default(doc.name, is_default)

	wanted = set(_parse(items) or [])
	for code in frappe.get_all("Item", filters={"quality_inspection_template": doc.name}, pluck="name"):
		if code not in wanted:
			frappe.db.set_value("Item", code, "quality_inspection_template", None)
	for code in wanted:
		frappe.db.set_value("Item", code, {
			"quality_inspection_template": doc.name,
			"inspection_required_before_purchase": cint(on_receipt),
			"inspection_required_before_delivery": cint(before_delivery),
		})
	return doc.name


def _set_default(template, is_default):
	"""Mark `template` the default checklist (or unmark it). Only one checklist is
	the default, so marking one clears the previous."""
	if cint(is_default):
		for other in frappe.get_all(QI + " Template",
		                            filters={"custom_is_default": 1, "name": ["!=", template]},
		                            pluck="name"):
			frappe.db.set_value(QI + " Template", other, "custom_is_default", 0)
	frappe.db.set_value(QI + " Template", template, "custom_is_default", 1 if cint(is_default) else 0)


@frappe.whitelist()
def set_default_template(template, is_default=1):
	"""The checklist list's "Set as default" / "Remove default" buttons."""
	frappe.has_permission(QI + " Template", "write", throw=True)
	if not frappe.db.exists(QI + " Template", template):
		frappe.throw(_("Checklist {0} not found").format(template))
	_set_default(template, is_default)
	return templates()
