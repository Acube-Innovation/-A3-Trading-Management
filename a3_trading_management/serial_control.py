# Implements: automatic chassis/serial creation on work order (task 8) and the
# serial's passage through the production stages into stock (task 9).
"""Work Order -> Trailer Serial.

One serial record per trailer unit, reserved to the work order that builds it, so
a work order for three trailers creates three serials. The chassis number is
issued at creation and frozen from then on.

Hooked on Work Order doc_events rather than called from the portal API, so a work
order raised from the Desk, from an import or from the portal all mint serials the
same way — there is no back door that produces a trailer with no chassis number.

Serials are minted only for items ticked `custom_is_trailer`. Without that gate a
work order for brake pads would be handed a chassis number.
"""

import frappe
from frappe import _
from frappe.utils import cint, flt, now_datetime

STAGES = ["Material", "Fabrication", "Assembly", "Paint", "QC", "Delivery"]
FINAL_STAGE = "Delivery"


def is_trailer_item(item_code):
	if not item_code:
		return False
	return bool(frappe.db.get_value("Item", item_code, "custom_is_trailer"))


# ---------------------------------------------------------------------------
# Task 8 — creation
# ---------------------------------------------------------------------------

def create_serials(doc, method=None):
	"""after_insert on Work Order: one Trailer Serial per unit."""
	if not is_trailer_item(doc.production_item):
		return
	existing = frappe.db.count("Trailer Serial", {"work_order": doc.name})
	if existing:
		return  # already minted; never double-stamp

	rule = frappe.get_single("Chassis Numbering Rule")
	if not rule.confirmed_by_redlines:
		frappe.throw(
			_("Chassis numbering has not been confirmed yet, so no chassis number can be "
			  "issued for this work order. Set the format in <b>Chassis Numbering Rule</b> "
			  "and tick <b>Format Confirmed by Redlines</b> first."),
			title=_("Chassis Numbering Rule not confirmed"),
		)

	for _i in range(max(cint(doc.qty), 1)):
		serial = frappe.new_doc("Trailer Serial")
		serial.chassis_number = rule.issue_next()
		serial.trailer_type = doc.production_item
		serial.work_order = doc.name
		serial.company = doc.company
		serial.production_stage = doc.get("custom_production_stage") or "Material"
		serial.status = "In Production"
		serial.flags.ignore_permissions = True
		serial.insert(ignore_permissions=True)


# ---------------------------------------------------------------------------
# Task 9 — through the stages and into stock
# ---------------------------------------------------------------------------

def sync_stage(doc, method=None):
	"""on_update on Work Order: carry the stage onto this order's serials.

	The trailer's own record has to be able to answer "where is it on the line?"
	without reading back through the work order.
	"""
	if not is_trailer_item(doc.production_item):
		return
	stage = doc.get("custom_production_stage")
	if not stage:
		return

	serials = frappe.get_all(
		"Trailer Serial", filters={"work_order": doc.name}, fields=["name", "production_stage", "status"]
	)
	if not serials:
		return

	fg_warehouse = doc.get("fg_warehouse")
	# A trailer is only in stock once production has actually been posted. Reaching
	# the Delivery stage on its own is just a field change: if the Manufacture stock
	# entry has not gone through, the serial would claim stock the ledger does not
	# have, and the delivery note would then fail on negative stock.
	produced = flt(doc.get("produced_qty"))
	completed = (
		stage == FINAL_STAGE
		and doc.docstatus == 1
		and (produced > 0 or (doc.get("status") or "") == "Completed")
	)

	finished = []
	for row in serials:
		values = {}
		if row.production_stage != stage:
			values["production_stage"] = stage
		if completed and row.status == "In Production":
			# On completion the finished trailer enters the warehouse against its
			# serial — that is what makes stock a list of identified units.
			values["status"] = "In Stock"
			if fg_warehouse:
				values["warehouse"] = fg_warehouse
			finished.append(row.name)
		if values:
			frappe.db.set_value("Trailer Serial", row.name, values, update_modified=False)




def on_work_order_cancel(doc, method=None):
	"""A cancelled work order builds nothing; its serials must not sit in stock."""
	if not is_trailer_item(doc.production_item):
		return
	for name in frappe.get_all("Trailer Serial", filters={"work_order": doc.name}, pluck="name"):
		# The chassis number is kept: it was issued, and reissuing it to another
		# trailer would put the same number on two units.
		frappe.db.set_value(
			"Trailer Serial", name, {"status": "Scrapped", "warehouse": None}, update_modified=False
		)


# ---------------------------------------------------------------------------
# Read helpers used by the portal screens (tasks 10 and 11)
# ---------------------------------------------------------------------------

@frappe.whitelist()
def get_work_order_serials(work_order):
	frappe.has_permission("Trailer Serial", "read", throw=True)
	return frappe.get_all(
		"Trailer Serial",
		filters={"work_order": work_order},
		fields=["name", "serial_no", "chassis_number", "production_stage", "status",
		        "warehouse", "chassis_embossed"],
		order_by="creation asc",
	)


def on_manufacture_entry(doc, method=None):
	"""A finished trailer enters the warehouse when the Manufacture entry posts.

	Advancing the work order to Delivery saves the stage first and posts the stock
	entry afterwards, so at save time `produced_qty` is still zero. This is the
	moment production actually happened, so it is the moment the serial can honestly
	claim to be in stock.
	"""
	if (doc.get("purpose") or doc.get("stock_entry_type")) not in ("Manufacture",):
		return
	work_order = doc.get("work_order")
	if not work_order:
		return
	try:
		wo = frappe.get_doc("Work Order", work_order)
	except frappe.DoesNotExistError:
		return
	sync_stage(wo)


def set_stock_entry_type(doc, method=None):
	"""ERPNext's `make_stock_entry` sets `purpose` but not `stock_entry_type`, which is
	the mandatory field on Stock Entry. A3 Trading's manufacturing screen completes a
	work order through it, so without this the entry never posts and the work order
	completes with nothing in the warehouse. Done here, on the document, so
	A3 Trading needs no edit and every route to a Stock Entry is covered."""
	if not doc.get("stock_entry_type") and doc.get("purpose"):
		if frappe.db.exists("Stock Entry Type", doc.purpose):
			doc.stock_entry_type = doc.purpose

