# Implements: the Serial and Chassis Register (task 10) and the Chassis Plate
# Print embossing sheet (task 11).
"""Read/write surface for the two Phase 2 portal screens.

The register is deliberately a plain filtered read over Trailer Serial: every
serial A3 has ever issued, searchable by chassis number, trailer type, work order,
stage, warehouse or owner. Nothing is computed that the record does not already
hold, so the register can never disagree with the trailer.
"""

import frappe
from frappe import _
from frappe.utils import cint, now_datetime

FIELDS = [
	"name", "serial_no", "chassis_number", "trailer_type", "work_order",
	"production_stage", "status", "warehouse", "current_owner", "owner_name",
	"warranty_start_date", "warranty_expiry_date", "chassis_embossed",
	"embossed_on", "creation",
]


@frappe.whitelist()
def list_serials(search=None, status=None, stage=None, warehouse=None, embossed=None, limit=200):
	"""Every serial ever issued, narrowed by whichever filters are supplied."""
	frappe.has_permission("Trailer Serial", "read", throw=True)

	filters = {}
	if status:
		filters["status"] = status
	if stage:
		filters["production_stage"] = stage
	if warehouse:
		filters["warehouse"] = warehouse
	if embossed in ("0", "1", 0, 1):
		filters["chassis_embossed"] = cint(embossed)

	or_filters = None
	if search:
		like = f"%{search}%"
		# Searchable by any of the things on the register, as the scope asks.
		or_filters = {
			"chassis_number": ["like", like],
			"serial_no": ["like", like],
			"trailer_type": ["like", like],
			"work_order": ["like", like],
			"warehouse": ["like", like],
			"current_owner": ["like", like],
			"owner_name": ["like", like],
		}

	rows = frappe.get_all(
		"Trailer Serial", filters=filters, or_filters=or_filters,
		fields=FIELDS, order_by="creation desc", limit_page_length=cint(limit) or 200,
	)
	today = frappe.utils.nowdate()
	for r in rows:
		r["warranty_state"] = _warranty_state(r, today)
	return rows


def _warranty_state(row, today):
	"""The warranty position the register shows — plain, not inferred."""
	if not row.get("warranty_expiry_date"):
		return "Not set"
	if str(row["warranty_expiry_date"]) < today:
		return "Expired"
	return "In warranty"


@frappe.whitelist()
def get_summary():
	counts = {}
	for row in frappe.get_all(
		"Trailer Serial", fields=["status", "count(name) as n"], group_by="status"
	):
		counts[row.status] = row.n
	return {
		"total": frappe.db.count("Trailer Serial"),
		"by_status": counts,
		"awaiting_embossing": frappe.db.count("Trailer Serial", {"chassis_embossed": 0}),
	}


# ---------------------------------------------------------------------------
# Task 11 — the embossing sheet
# ---------------------------------------------------------------------------

@frappe.whitelist()
def get_plate_batch(serials=None, work_order=None):
	"""The numbers the fabrication bay is about to emboss.

	Takes either an explicit set of serials or a whole work order, so the bay can
	print one trailer or the batch it is working through.
	"""
	frappe.has_permission("Trailer Serial", "read", throw=True)
	names = _resolve(serials, work_order)
	if not names:
		return []
	return frappe.get_all(
		"Trailer Serial", filters={"name": ["in", names]},
		fields=["name", "serial_no", "chassis_number", "trailer_type", "work_order",
		        "chassis_embossed", "embossed_on"],
		order_by="creation asc",
	)


@frappe.whitelist()
def mark_embossed(serials=None, work_order=None):
	"""Record the embossing step against each serial.

	Written straight to the row: the chassis number itself is immutable, and this
	only records that the plate for it has been stamped.
	"""
	frappe.has_permission("Trailer Serial", "write", throw=True)
	names = _resolve(serials, work_order)
	stamped = 0
	for name in names:
		if frappe.db.get_value("Trailer Serial", name, "chassis_embossed"):
			continue
		frappe.db.set_value(
			"Trailer Serial", name,
			{"chassis_embossed": 1, "embossed_on": now_datetime(), "embossed_by": frappe.session.user},
			update_modified=False,
		)
		stamped += 1
	frappe.db.commit()
	return {"stamped": stamped, "requested": len(names)}


def _resolve(serials, work_order):
	if serials:
		if isinstance(serials, str):
			serials = frappe.parse_json(serials)
		return [s for s in (serials or []) if s]
	if work_order:
		return frappe.get_all("Trailer Serial", filters={"work_order": work_order}, pluck="name")
	return []
