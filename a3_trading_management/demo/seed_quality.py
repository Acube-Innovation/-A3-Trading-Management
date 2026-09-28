"""Demo data for the Quality screen.

    bench --site <site> execute a3_trading_management.demo.seed_quality.run

Loads on top of demo.seed:

* three checklists — trailer final QC on every trailer model, and incoming
  checks for steel and for tyres and rims (those items are ticked "inspection
  required before purchase");
* inspection history for the work orders already delivered: most passed first
  time, two failed and passed on re-check, the oldest recorded from the
  Manufacturing screen;
* one work order at QC with a failed inspection (Re-inspect) and the rest left
  waiting;
* two draft goods receipts waiting for incoming inspection, and one received
  lot already inspected.

Goes through a3_trading_management.api.quality like the screen does. Runs once;
a second run skips unless `force=1`. RAW-STEEL and TRL-FLATBED's delivery flag
are left alone: the e2e cycle test receives RAW-STEEL without an inspection.
"""

import json
import random

import frappe
from frappe.utils import add_days, getdate, nowdate

from a3_trading_management.api import buying, quality

TRAILER_CHECKLIST = "Trailer Final QC"
TRAILER_CHECKS = [
	# specification, numeric, min, max, expected
	("Brake lights & indicators", 0, 0, 0, "OK"),
	("Air brake leak-down (psi/min)", 1, 0, 3, ""),
	("Tyre pressure (psi)", 1, 100, 120, ""),
	("Wheel nut torque (Nm)", 1, 600, 650, ""),
	("Paint film thickness (microns)", 1, 120, 200, ""),
	("Weld seams — visual", 0, 0, 0, "No cracks"),
	("Landing gear operation", 0, 0, 0, "OK"),
	("Chassis number embossed & legible", 0, 0, 0, "OK"),
	("Axle alignment deviation (mm)", 1, 0, 3, ""),
]

INCOMING = {
	"Steel Plate & Beam — Incoming": {
		"items": ["RM-PLATE-6MM", "RM-IBEAM-400"],
		"checks": [
			("Mill test certificate", 0, 0, 0, "Received"),
			("Plate thickness (mm)", 1, 5.8, 6.2, ""),
			("Surface rust / pitting", 0, 0, 0, "None"),
			("Straightness deviation (mm/m)", 1, 0, 2, ""),
		],
	},
	"Tyres & Rims — Incoming": {
		"items": ["RM-TYRE-315", "RM-RIM-225"],
		"checks": [
			("DOT age (months)", 1, 0, 12, ""),
			("Tread depth (mm)", 1, 15, 20, ""),
			("Sidewall / rim damage", 0, 0, 0, "None"),
		],
	},
}

# Delivered work orders that failed first time, and what was wrong.
FAILED_FIRST = {
	"MFG-WO-2026-00022": ({"Paint film thickness (microns)": "104"},
	                      "Low paint build on the rear bumper and mudguard brackets. Back to the paint booth for a third coat."),
	"MFG-WO-2026-00025": ({"Air brake leak-down (psi/min)": "4.6"},
	                      "Air leak at the trailer control valve. Valve re-seated and lines re-tested."),
}
# At QC now, failed: shows as Re-inspect.
REINSPECT = ("MFG-WO-2026-00033",
             {"Wheel nut torque (Nm)": "575", "Axle alignment deviation (mm)": "4.5"},
             "Rear axle out of alignment and nut torque low on the left hub. Returned to assembly.")


def _checks(rows):
	return [{"specification": s, "numeric": n, "min_value": lo, "max_value": hi, "value": v}
	        for s, n, lo, hi, v in rows]


def _reading(rng, p, bad=None):
	"""A plausible reading for one check, or the given failing one."""
	if bad is not None:
		return str(bad), "Rejected"
	if p["numeric"]:
		lo, hi = p["min_value"], p["max_value"]
		span = hi - lo
		v = rng.uniform(lo + span * 0.15, hi - span * 0.15)
		return ("{0:.1f}".format(v) if span < 20 else str(int(round(v)))), "Accepted"
	return p["value"] or "OK", "Accepted"


def _inspect(rng, ref_type, ref_name, template, bad=None, remarks="", item_code=None,
             child_row=None, on=None):
	params = quality._parameters(template)
	readings = []
	for p in params:
		value, result = _reading(rng, p, (bad or {}).get(p["specification"]))
		readings.append({"specification": p["specification"], "reading": value, "result": result})
	res = quality.record_inspection(ref_type, ref_name, item_code=item_code, readings=json.dumps(readings),
	                                remarks=remarks, template=template, child_row=child_row)
	if on:
		frappe.db.set_value("Quality Inspection", res["name"], "report_date", on, update_modified=False)
	return res


def _delivered_on(wo):
	d = frappe.db.sql(
		"""select max(entered_on) from `tabWorkshop Work Order Stage Log`
		   where parent = %s and parenttype = 'Work Order' and stage = 'Delivery'""", wo)[0][0]
	return getdate(d) if d else None


def run(force=0):
	if frappe.db.exists("Quality Inspection Template", TRAILER_CHECKLIST) and not int(force):
		print("Quality demo data was loaded before (pass force=1 to add it again). Done.")
		return
	frappe.set_user("Administrator")
	rng = random.Random(28)

	print("Checklists")
	trailers = frappe.get_all("Item", filters={"custom_is_trailer": 1, "disabled": 0}, pluck="name")
	quality.save_template(TRAILER_CHECKLIST, json.dumps(_checks(TRAILER_CHECKS)),
	                      items=json.dumps(trailers), name=TRAILER_CHECKLIST if frappe.db.exists(
		                      "Quality Inspection Template", TRAILER_CHECKLIST) else None)
	print(f"  {TRAILER_CHECKLIST}: {len(TRAILER_CHECKS)} checks, {len(trailers)} trailer models")
	for name, spec in INCOMING.items():
		items = [i for i in spec["items"] if frappe.db.exists("Item", i)]
		quality.save_template(name, json.dumps(_checks(spec["checks"])), items=json.dumps(items), on_receipt=1,
		                      name=name if frappe.db.exists("Quality Inspection Template", name) else None)
		print(f"  {name}: {', '.join(items)} (inspect on goods receipt)")
	frappe.db.commit()

	print("\nInspection history (delivered work orders)")
	delivered = frappe.get_all(
		"Work Order",
		filters={"docstatus": 1, "custom_production_stage": "Delivery"},
		fields=["name", "production_item"], order_by="name asc",
	)
	for wo in delivered:
		if quality.latest_inspection("Work Order", wo.name):
			continue  # inspected already
		on = _delivered_on(wo.name)
		if not on:
			continue  # older than the stage log; nothing to date it by
		if wo.name <= "MFG-WO-2026-00013":
			# Before the checklist existed: signed off on the Manufacturing screen.
			qi = quality._make_inspection(
				"Work Order", wo.name, wo.production_item, [], status="Accepted",
				remarks="QC passed on the Manufacturing screen.", sample_size=1, auto=True,
			)
			frappe.db.set_value("Quality Inspection", qi.name, "report_date", on, update_modified=False)
			print(f"  {wo.name}  {qi.name} Accepted (from Manufacturing)")
			continue
		if wo.name in FAILED_FIRST:
			bad, why = FAILED_FIRST[wo.name]
			r = _inspect(rng, "Work Order", wo.name, TRAILER_CHECKLIST, bad=bad, remarks=why, on=add_days(on, -1))
			print(f"  {wo.name}  {r['name']} Rejected — {why[:50]}…")
		r = _inspect(rng, "Work Order", wo.name, TRAILER_CHECKLIST,
		             remarks="All checks within limits. Released to stock.", on=on)
		print(f"  {wo.name}  {r['name']} Accepted")
	frappe.db.commit()

	print("\nWaiting at QC")
	wo, bad, why = REINSPECT
	if frappe.db.get_value("Work Order", wo, "custom_production_stage") == "QC" \
			and not quality.latest_inspection("Work Order", wo):
		r = _inspect(rng, "Work Order", wo, TRAILER_CHECKLIST, bad=bad, remarks=why)
		print(f"  {wo}  {r['name']} Rejected -> Re-inspect")
	for row in quality.pending():
		if row["reference_type"] == "Work Order":
			print(f"  {row['reference_name']}  {row['state']}")
	frappe.db.commit()

	print("\nIncoming (goods receipts)")
	company = frappe.defaults.get_defaults().get("company") or frappe.get_all("Company", pluck="name")[0]
	stores = "Stores - " + frappe.db.get_value("Company", company, "abbr")

	def draft_receipt(supplier_name, lines):
		supplier = frappe.db.get_value("Supplier", {"supplier_name": supplier_name}, "name")
		if not supplier:
			return None
		po = buying.create_purchase_order(
			supplier, [dict(l, warehouse=stores) for l in lines],
			schedule_date=add_days(nowdate(), 3), company=company,
		)["name"]
		buying.submit_purchase_order(po)
		return buying.receive_against_order(po)["name"]

	gr = draft_receipt("Desert Tyre Distributors", [{"item_code": "RM-TYRE-315", "qty": 24, "rate": 950},
	                                                {"item_code": "RM-RIM-225", "qty": 24, "rate": 310}])
	print(f"  {gr}  tyres and rims — waiting for inspection")
	gr = draft_receipt("Gulf Steel Trading LLC", [{"item_code": "RM-PLATE-6MM", "qty": 30, "rate": 540},
	                                             {"item_code": "RM-IBEAM-400", "qty": 60, "rate": 185}])
	if gr:
		# The plates are checked already; the beams still wait.
		row = frappe.db.get_value("Purchase Receipt Item", {"parent": gr, "item_code": "RM-PLATE-6MM"}, "name")
		r = _inspect(rng, "Purchase Receipt", gr, "Steel Plate & Beam — Incoming", item_code="RM-PLATE-6MM",
		             child_row=row, remarks="Heat numbers match the mill certificate.")
		print(f"  {gr}  plates {r['name']} Accepted; I-beams waiting")
	frappe.db.commit()

	b = quality.get_board()
	print(f"\nQuality demo data loaded: {b['stats']['waiting']} waiting, {len(b['inspections'])} inspections, "
	      f"{len(b['templates'])} checklists.")
