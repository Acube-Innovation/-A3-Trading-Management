"""Load a realistic set of demo data: masters plus a month of trading.

    bench --site <site> execute a3_trading_management.demo.seed.run

Masters (groups, territories, UOMs, raw materials, four more trailer models with
parts lists and operations, workstations, suppliers with compliance papers,
customers with CRM flags, prices) are created only when missing, so re-running
is safe. The transactions — requisitions, orders, receipts, bills, payments,
work orders through the six stages, sales orders, deliveries, invoices,
receipts and journals — are posted once; a second run skips them unless
`force=1`.

Every transaction goes through the same portal endpoints the screens call
(a3_trading_management.api.*), so the data is exactly what real use would leave
behind: chassis numbers issued by the numbering rule, stock posted by the
Manufacture entry, bills and payments in the standard ledger. It posts real
documents: run it on a demo or test site, never on live books.
"""

import math

import frappe
from frappe.utils import add_days, flt, nowdate

from a3_trading_management.api import buying, manufacturing, selling, serial_register

# ---------------------------------------------------------------------------
# Master data
# ---------------------------------------------------------------------------

UOMS = [("Meter", 0), ("Kg", 0), ("Set", 1)]

ITEM_GROUPS = ["Raw Material", "Components", "Consumables"]

SUPPLIER_GROUPS = ["Steel", "Running Gear", "Tyres & Wheels", "Paints & Consumables",
                   "Electrical", "Hydraulics", "Body Accessories"]

CUSTOMER_GROUPS = ["Logistics", "Construction", "Port & Container", "Government", "Individual"]

TERRITORIES = {
	"United Arab Emirates": ["Dubai", "Abu Dhabi", "Sharjah", "Ras Al Khaimah"],
	"Oman": [],
	"Saudi Arabia": [],
}

# code: (name, group, uom, buying price, supplier key)
RAW_ITEMS = {
	"RM-IBEAM-400": ("I-Beam 400 mm", "Raw Material", "Meter", 185, "steel"),
	"RM-PLATE-6MM": ("Steel Plate 6 mm, 2.5 x 1.25 m", "Raw Material", "Nos", 540, "steel"),
	"RM-WELD-WIRE": ("MIG Welding Wire 1.2 mm", "Consumables", "Kg", 14, "steel"),
	"RM-AXLE-13T": ("Axle 13 T, Drum Brake", "Components", "Nos", 3200, "gear"),
	"RM-SUSP-MECH": ("Mechanical Suspension Set", "Components", "Set", 1450, "gear"),
	"RM-LANDING-GEAR": ("Landing Gear, Two-Speed", "Components", "Set", 1100, "gear"),
	"RM-KINGPIN-2": ("King Pin 2 inch", "Components", "Nos", 380, "gear"),
	"RM-BRAKE-CH": ("Brake Chamber 30/30", "Components", "Nos", 260, "gear"),
	"RM-TYRE-315": ("Tyre 315/80 R22.5", "Components", "Nos", 950, "tyres"),
	"RM-RIM-225": ("Steel Wheel Rim 22.5 x 9", "Components", "Nos", 310, "tyres"),
	"RM-PRIMER": ("Epoxy Primer", "Consumables", "Litre", 38, "paint"),
	"RM-ENAMEL": ("PU Enamel Paint", "Consumables", "Litre", 52, "paint"),
	"RM-LIGHT-KIT": ("LED Lighting Kit", "Components", "Set", 650, "electrical"),
	"RM-HARNESS": ("7-Pin Wiring Harness", "Components", "Nos", 420, "electrical"),
	"RM-AIRLINE": ("Air Line Kit", "Components", "Set", 480, "electrical"),
	"RM-HYD-CYL": ("Hydraulic Tipping Cylinder", "Components", "Nos", 9200, "hydraulics"),
	"RM-CURTAIN": ("Curtain and Roof Kit 13.6 m", "Components", "Set", 7800, "body"),
	"RM-TWISTLOCK": ("Container Twist Lock", "Components", "Nos", 145, "body"),
}

# Bought only just enough of these, so Live Stock shows them as low-stock alerts.
RUN_LOW = {"RM-TYRE-315", "RM-BRAKE-CH", "RM-HYD-CYL", "RM-PRIMER"}

_RUNNING_GEAR_3AX = {"RM-AXLE-13T": 3, "RM-SUSP-MECH": 3, "RM-TYRE-315": 12, "RM-RIM-225": 12,
                     "RM-BRAKE-CH": 6, "RM-LANDING-GEAR": 1, "RM-KINGPIN-2": 1,
                     "RM-LIGHT-KIT": 1, "RM-HARNESS": 1, "RM-AIRLINE": 1}

# code: (name, selling price, BOM lines, operation minutes by operation)
TRAILERS = {
	"TRL-LOWBED-3AX": (
		"Lowbed Trailer 3-Axle 60 T", 118000,
		dict(_RUNNING_GEAR_3AX, **{"RM-IBEAM-400": 24, "RM-PLATE-6MM": 10, "RM-PRIMER": 20,
		                           "RM-ENAMEL": 25, "RM-WELD-WIRE": 15}),
		{"Cutting": 300, "Welding & Fabrication": 1080, "Assembly": 600, "Painting": 420, "Quality Inspection": 120},
	),
	"TRL-CURTAIN-3AX": (
		"Curtainsider Trailer 3-Axle 13.6 m", 96000,
		dict(_RUNNING_GEAR_3AX, **{"RM-IBEAM-400": 20, "RM-PLATE-6MM": 6, "RM-CURTAIN": 1,
		                           "RM-PRIMER": 15, "RM-ENAMEL": 18, "RM-WELD-WIRE": 10}),
		{"Cutting": 240, "Welding & Fabrication": 840, "Assembly": 720, "Painting": 360, "Quality Inspection": 90},
	),
	"TRL-SKELETAL-40": (
		"Skeletal Container Trailer 40 ft", 62000,
		dict(_RUNNING_GEAR_3AX, **{"RM-IBEAM-400": 16, "RM-PLATE-6MM": 3, "RM-TWISTLOCK": 4,
		                           "RM-PRIMER": 12, "RM-ENAMEL": 14, "RM-WELD-WIRE": 8}),
		{"Cutting": 180, "Welding & Fabrication": 600, "Assembly": 420, "Painting": 300, "Quality Inspection": 60},
	),
	"TRL-TIPPER-2AX": (
		"Tipper Trailer 2-Axle 30 cbm", 104000,
		{"RM-IBEAM-400": 14, "RM-PLATE-6MM": 14, "RM-AXLE-13T": 2, "RM-SUSP-MECH": 2, "RM-TYRE-315": 8,
		 "RM-RIM-225": 8, "RM-BRAKE-CH": 4, "RM-LANDING-GEAR": 1, "RM-KINGPIN-2": 1, "RM-HYD-CYL": 1,
		 "RM-LIGHT-KIT": 1, "RM-HARNESS": 1, "RM-AIRLINE": 1, "RM-PRIMER": 18, "RM-ENAMEL": 22,
		 "RM-WELD-WIRE": 18},
		{"Cutting": 300, "Welding & Fabrication": 1200, "Assembly": 540, "Painting": 420, "Quality Inspection": 120},
	),
}

# operation: (workstation, hour rate)
OPERATIONS = {
	"Cutting": ("Cutting Bay", 120),
	"Welding & Fabrication": ("Welding Bay", 150),
	"Assembly": ("Assembly Line", 130),
	"Painting": ("Paint Booth", 160),
	"Quality Inspection": ("QC Bay", 110),
}

# key: (name, group, mobile, email, TRN, licence expiry, VAT expiry, establishment expiry, status)
# Expiry dates are day offsets from today: negative has lapsed, under 30 is Expiring.
SUPPLIERS = {
	"steel": ("Gulf Steel Trading LLC", "Steel", "+971 4 555 0101", "sales@gulfsteel.example",
	          "100245678900003", 400, 300, 500, "Compliant"),
	"gear": ("Axle & Running Gear Co. LLC", "Running Gear", "+971 6 555 0102", "orders@runninggear.example",
	         "100245678900103", 250, 380, 290, "Compliant"),
	"tyres": ("Desert Tyre Distributors", "Tyres & Wheels", "+971 4 555 0103", "info@deserttyre.example",
	          "100245678900203", 300, 12, 220, "Compliant"),
	"paint": ("BrightCoat Paints Industries", "Paints & Consumables", "+971 6 555 0104", "sales@brightcoat.example",
	          "100245678900303", 330, 410, 365, "Compliant"),
	"electrical": ("Voltline Auto Electricals", "Electrical", "+971 4 555 0105", "voltline@voltline.example",
	               "100245678900403", 190, 260, 240, "Compliant"),
	"hydraulics": ("Hydrotech Hydraulics FZE", "Hydraulics", "+971 7 555 0106", "sales@hydrotech.example",
	               "100245678900503", 420, 300, 350, "Compliant"),
	"body": ("CargoFit Trailer Accessories", "Body Accessories", "+971 6 555 0107", "orders@cargofit.example",
	         "100245678900603", 20, 280, 310, "Compliant"),
	"lapsed": ("Northern Metals Supply", "Steel", "+971 4 555 0108", "info@northernmetals.example",
	           "100245678900703", -45, 120, 60, "Suspended"),
}

# key: (name, group, territory, type, mobile, email, TRN, crm flags)
CUSTOMERS = {
	"gulf_horizon": ("Gulf Horizon Logistics LLC", "Logistics", "Dubai", "Company", "+971 50 555 2001",
	                 "fleet@gulfhorizon.example", "100399887700003",
	                 {"custom_vip": 1, "custom_credit_limit_amount": 500000}),
	"desert_line": ("Desert Line Transport LLC", "Logistics", "Sharjah", "Company", "+971 50 555 2002",
	                "ops@desertline.example", "100399887700103", {"custom_credit_limit_amount": 250000}),
	"al_noor": ("Al Noor Heavy Haulage", "Construction", "Abu Dhabi", "Company", "+971 50 555 2003",
	            "procurement@alnoor.example", "100399887700203",
	            {"custom_vip": 1, "custom_credit_limit_amount": 400000}),
	"falcon": ("Falcon Freight Services", "Logistics", "Dubai", "Company", "+971 55 555 2004",
	           "accounts@falconfreight.example", "100399887700303", {"custom_credit_limit_amount": 150000}),
	"blue_dune": ("Blue Dune Contracting LLC", "Construction", "Ras Al Khaimah", "Company", "+971 55 555 2005",
	              "admin@bluedune.example", "100399887700403", {"custom_credit_limit_amount": 200000}),
	"crescent": ("Crescent Port Services", "Port & Container", "Abu Dhabi", "Company", "+971 56 555 2006",
	             "fleet@crescentport.example", "100399887700503", {"custom_credit_limit_amount": 300000}),
	"sandstone": ("Sandstone Construction Co.", "Construction", "Sharjah", "Company", "+971 56 555 2007",
	              "purchase@sandstone.example", "100399887700603", {}),
	"oasis": ("Oasis Container Movers", "Port & Container", "Oman", "Company", "+968 9555 2008",
	          "info@oasiscontainer.example", None, {}),
	"rashid": ("Rashid Al Mansoori", "Individual", "Dubai", "Individual", "+971 50 555 2009",
	           "rashid.m@mail.example", None, {"custom_credit_hold": 1, "custom_birthday": "1981-03-14"}),
	"swift": ("Swift Cargo Carriers", "Logistics", "Saudi Arabia", "Company", "+966 55 555 2010",
	          "swiftcargo@mail.example", None,
	          {"custom_blacklist": 1, "custom_blacklist_reason": "Two cheques returned unpaid in 2025"}),
}

# ---------------------------------------------------------------------------
# The month's trading
# ---------------------------------------------------------------------------

# Work orders built all the way into stock: (item, qty, specification).
COMPLETED_BUILDS = [
	("TRL-LOWBED-3AX", 2, "Hydraulic ramps, 60 T rating, RAL 5010 blue, customer logo on side rails."),
	("TRL-CURTAIN-3AX", 3, "Grey curtains, galvanised side posts, 34 pallet floor."),
	("TRL-SKELETAL-40", 4, "8 twist locks (20/40 ft), black chassis."),
	("TRL-SKELETAL-40", 2, "4 twist locks, 40 ft only, black chassis."),
	("TRL-TIPPER-2AX", 2, "Rear-hinged tailgate, Hardox floor, yellow body."),
]

# Work orders left on the floor: (item, qty, stage reached, specification).
IN_PROGRESS_BUILDS = [
	("TRL-LOWBED-3AX", 1, "Paint", "Extendable deck, RAL 3000 red."),
	("TRL-CURTAIN-3AX", 2, "Fabrication", "Blue curtains, double-deck rails."),
	("TRL-SKELETAL-40", 2, "QC", "12 twist locks, 20/40/45 ft combination."),
	("TRL-TIPPER-2AX", 1, "Assembly", "Side tipper, grey body."),
	("TRL-LOWBED-3AX", 1, "Material", "Standard 3-axle lowbed, stock build."),
]

STAGES = ["Material", "Fabrication", "Assembly", "Paint", "QC", "Delivery"]

STAGE_NOTES = {
	"Fabrication": "Material issued from Stores; cutting started.",
	"Assembly": "Chassis welded and checked for straightness.",
	"Paint": "Running gear and electrics fitted.",
	"QC": "Two coats applied, cured overnight.",
	"Delivery": "QC passed: brakes, lights, air lines tested.",
}


def run(force=0):
	company = frappe.defaults.get_defaults().get("company") or frappe.get_all("Company", pluck="name")[0]
	abbr = frappe.db.get_value("Company", company, "abbr")
	ctx = frappe._dict(
		company=company,
		stores=f"Stores - {abbr}",
		fg=f"Finished Goods - {abbr}",
		cost_center=frappe.db.get_value("Company", company, "cost_center"),
		abbr=abbr,
	)

	print("Masters")
	_masters(ctx)

	marker = frappe.db.exists("Sales Order", {"customer": CUSTOMERS["gulf_horizon"][0]})
	if marker and not int(force):
		print("\nTransactions were loaded before (pass force=1 to add another set). Done.")
		return

	print("\nBuying")
	_buying(ctx)
	print("\nSales orders")
	orders = _selling_orders(ctx)
	print("\nProduction")
	_production(ctx, orders)
	print("\nSelling")
	_selling(ctx, orders)
	print("\nAccounts")
	_journals(ctx)
	print("\nDemo data loaded.")


# ---------------------------------------------------------------------------

def _masters(ctx):
	for uom, whole in UOMS:
		_ensure("UOM", uom, {"uom_name": uom, "must_be_whole_number": whole})

	_ensure_tree("Item Group", "All Item Groups", "item_group_name", "parent_item_group", ITEM_GROUPS)
	_ensure_tree("Supplier Group", "All Supplier Groups", "supplier_group_name", "parent_supplier_group",
	             SUPPLIER_GROUPS)
	_ensure_tree("Customer Group", "All Customer Groups", "customer_group_name", "parent_customer_group",
	             CUSTOMER_GROUPS)
	_ensure_tree("Territory", "All Territories", "territory_name", "parent_territory",
	             [t for t in TERRITORIES if TERRITORIES[t]], leaf=False)
	_ensure_tree("Territory", "All Territories", "territory_name", "parent_territory",
	             [t for t in TERRITORIES if not TERRITORIES[t]])
	for parent, children in TERRITORIES.items():
		_ensure_tree("Territory", parent, "territory_name", "parent_territory", children)
	print("  groups, territories and units")

	need = _raw_need()
	for code, (name, group, uom, price, _sup) in RAW_ITEMS.items():
		level = max(1, math.ceil(need.get(code, 0) * 0.25))
		_ensure_item(ctx, code, name, group, uom, valuation_rate=price, reorder=(level, level * 2))
		_ensure_price(code, price, "Standard Buying")
	for code, (name, price, _bom, _ops) in TRAILERS.items():
		_ensure_item(ctx, code, name, "Trailers", "Nos", trailer=True)
		_ensure_price(code, price, "Standard Selling")
	print(f"  {len(RAW_ITEMS)} raw materials, {len(TRAILERS)} trailer models, prices")

	for op, (ws, rate) in OPERATIONS.items():
		_ensure("Workstation", ws, {"workstation_name": ws, "hour_rate_labour": rate})
		_ensure("Operation", op, {"name": op, "workstation": ws})
	for code, (_name, _price, lines, ops) in TRAILERS.items():
		if frappe.db.exists("BOM", {"item": code, "docstatus": 1, "is_active": 1}):
			continue
		bom = frappe.get_doc({
			"doctype": "BOM", "item": code, "quantity": 1, "company": ctx.company,
			"is_active": 1, "is_default": 1, "with_operations": 1,
			"rm_cost_as_per": "Price List", "buying_price_list": "Standard Buying",
			"currency": "AED",
			"items": [{"item_code": c, "qty": q} for c, q in lines.items()],
			"operations": [{"operation": o, "workstation": OPERATIONS[o][0], "time_in_mins": m}
			               for o, m in ops.items()],
		})
		bom.insert(ignore_permissions=True)
		bom.submit()
	print("  workstations, operations and parts lists")

	today = frappe.utils.getdate(nowdate())
	for key, (name, group, mobile, email, trn, lic, vat, est, status) in SUPPLIERS.items():
		existing = frappe.db.get_value("Supplier", {"supplier_name": name}, "name")
		result = buying.save_supplier(
			name=existing, supplier_name=name, mobile_no=mobile, email_id=email, tax_id=trn,
			trade_license_expiry=str(add_days(today, lic)), vat_cert_expiry=str(add_days(today, vat)),
			establishment_expiry=str(add_days(today, est)), compliance_status=status,
		)
		frappe.db.set_value("Supplier", result["name"], {
			"supplier_group": group,
			"custom_compliance_blocks_po": 1 if key == "lapsed" else 0,
			"custom_compliance_country": "United Arab Emirates",
		}, update_modified=False)
	print(f"  {len(SUPPLIERS)} suppliers with compliance papers")

	for key, (name, group, territory, ctype, mobile, email, trn, crm) in CUSTOMERS.items():
		if frappe.db.exists("Customer", {"customer_name": name}):
			continue
		doc = frappe.get_doc({
			"doctype": "Customer", "customer_name": name, "customer_type": ctype,
			"customer_group": group, "territory": territory,
			"mobile_no": mobile, "email_id": email, "tax_id": trn,
			**crm,
		})
		doc.insert(ignore_permissions=True)
	print(f"  {len(CUSTOMERS)} customers")
	frappe.db.commit()


def _raw_need():
	"""Raw material every build that reaches stock will consume, plus the one built to order."""
	need = {}
	builds = [(item, qty) for item, qty, _spec in COMPLETED_BUILDS] + [("TRL-CURTAIN-3AX", 1)]
	for item, qty in builds:
		for code, per in TRAILERS[item][2].items():
			need[code] = need.get(code, 0) + per * qty
	return need


# ---------------------------------------------------------------------------

def _buying(ctx):
	need = _raw_need()
	qty = {}
	for code in RAW_ITEMS:
		n = need.get(code, 0)
		qty[code] = n + 1 if code in RUN_LOW else math.ceil(n * 1.35) + 5

	def lines(supplier_key):
		return [{"item_code": c, "qty": qty[c], "rate": RAW_ITEMS[c][3], "warehouse": ctx.stores}
		        for c, row in RAW_ITEMS.items() if row[4] == supplier_key]

	def supplier(key):
		return frappe.db.get_value("Supplier", {"supplier_name": SUPPLIERS[key][0]}, "name")

	def via_requisition(key):
		req = buying.create_requisition(items=lines(key), company=ctx.company)
		buying.approve_requisition(req["name"])
		po = buying.requisition_to_order(req["name"], supplier(key))
		_price_order(po["name"])
		return req["name"], po["name"]

	def direct(key, rows=None):
		return buying.create_purchase_order(supplier(key), rows or lines(key), company=ctx.company)["name"]

	def receive_and_bill(po):
		buying.submit_purchase_order(po)
		gr = buying.receive_against_order(po)["name"]
		buying.submit_goods_receipt(gr)
		return gr

	# Steel: requisition -> order -> receipt -> bill -> paid in full.
	req, po = via_requisition("steel")
	gr = receive_and_bill(po)
	pi = _bill(gr)
	_pay(pi)
	print(f"  Steel         {req} -> {po} -> {gr} -> {pi}  paid in full")

	# Running gear: direct order, billed, half paid.
	po = direct("gear")
	gr = receive_and_bill(po)
	pi = _bill(gr)
	total = frappe.db.get_value("Purchase Invoice", pi, "outstanding_amount")
	_pay(pi, amount=round(flt(total) / 2, -2))
	print(f"  Running gear  {po} -> {gr} -> {pi}  part paid")

	# Tyres: requisition -> order -> receipt -> bill, not yet paid.
	req, po = via_requisition("tyres")
	gr = receive_and_bill(po)
	pi = _bill(gr)
	print(f"  Tyres         {req} -> {po} -> {gr} -> {pi}  unpaid")

	# Paint: the bill is held for approval.
	po = direct("paint")
	gr = receive_and_bill(po)
	pi = buying.bill_against(purchase_receipt=gr)["name"]
	buying.set_invoice_hold(pi, 1, "Enamel rate is 4 AED/L above the quotation; confirming with supplier.")
	print(f"  Paint         {po} -> {gr} -> {pi}  on hold")

	# Electrical: received, not billed yet (shows under Payments Missing).
	po = direct("electrical")
	gr = receive_and_bill(po)
	print(f"  Electrical    {po} -> {gr}  awaiting bill")

	for key in ("hydraulics", "body"):
		po = direct(key)
		gr = receive_and_bill(po)
		pi = _bill(gr)
		_pay(pi)
		print(f"  {key.title():13} {po} -> {gr} -> {pi}  paid in full")

	# A second steel order, approved and on its way (Incoming on Live Stock).
	po = direct("steel", [{"item_code": "RM-IBEAM-400", "qty": 120, "rate": 185, "warehouse": ctx.stores},
	                      {"item_code": "RM-PLATE-6MM", "qty": 40, "rate": 540, "warehouse": ctx.stores}])
	buying.submit_purchase_order(po)
	print(f"  Steel         {po}  approved, awaiting delivery")

	# A draft order for next month.
	po = direct("gear", [{"item_code": "RM-AXLE-13T", "qty": 12, "rate": 3200, "warehouse": ctx.stores}])
	print(f"  Running gear  {po}  draft")

	# Requisitions still in the office: one awaiting approval, one approved.
	req = buying.create_requisition(items=[
		{"item_code": "RM-TYRE-315", "qty": 48, "warehouse": ctx.stores},
		{"item_code": "RM-BRAKE-CH", "qty": 24, "warehouse": ctx.stores},
	], company=ctx.company)["name"]
	print(f"  Requisition   {req}  awaiting approval")
	req = buying.create_requisition(items=[
		{"item_code": "RM-HYD-CYL", "qty": 3, "warehouse": ctx.stores},
		{"item_code": "RM-PRIMER", "qty": 120, "warehouse": ctx.stores},
	], company=ctx.company)["name"]
	buying.approve_requisition(req)
	print(f"  Requisition   {req}  approved, not yet ordered")


def _bill(receipt):
	pi = buying.bill_against(purchase_receipt=receipt)["name"]
	buying.approve_purchase_invoice(pi)
	return pi


def _pay(purchase_invoice, amount=None):
	pe = buying.pay_supplier_invoice(purchase_invoice, amount=amount)["name"]
	buying.submit_payment(pe)
	return pe


def _price_order(name):
	"""A requisition carries no price; take the item's buying price, as the office would."""
	doc = frappe.get_doc("Purchase Order", name)
	for row in doc.items:
		if not flt(row.rate):
			row.rate = RAW_ITEMS[row.item_code][3]
	doc.save(ignore_permissions=True)
	frappe.db.commit()


# ---------------------------------------------------------------------------

def _selling_orders(ctx):
	"""Customer orders are taken first so one of them can have a trailer built for it."""
	orders = {}

	def order(key, item, qty, confirm=True, days=14):
		customer = frappe.db.get_value("Customer", {"customer_name": CUSTOMERS[key][0]}, "name")
		rate = TRAILERS[item][1] if item in TRAILERS else 50000
		so = selling.create_sales_order(customer, [{"item_code": item, "qty": qty, "rate": rate}],
		                                delivery_date=add_days(nowdate(), days), company=ctx.company)["name"]
		if confirm:
			selling.submit_sales_order(so)
		orders[key] = frappe._dict(name=so, item=item, qty=qty)
		return so

	order("gulf_horizon", "TRL-LOWBED-3AX", 2)
	order("desert_line", "TRL-CURTAIN-3AX", 2)
	order("al_noor", "TRL-SKELETAL-40", 3)
	order("falcon", "TRL-TIPPER-2AX", 1)
	order("crescent", "TRL-SKELETAL-40", 1)
	order("blue_dune", "TRL-CURTAIN-3AX", 1, days=30)
	order("sandstone", "TRL-TIPPER-2AX", 1)
	order("oasis", "TRL-CURTAIN-3AX", 1, days=21)
	order("rashid", "TRL-FLATBED", 1, confirm=False)
	for key, o in orders.items():
		print(f"  Sales order   {o.name}  {CUSTOMERS[key][0]}: {o.qty} x {o.item}")
	return orders


def _production(ctx, orders):
	def build(item, qty, spec, stage, sales_order=None):
		if sales_order:
			wo = selling.build_for_order(sales_order, item, qty)["work_order"]
			frappe.db.set_value("Work Order", wo, "description", spec, update_modified=False)
		else:
			wo = manufacturing.create_work_order(
				item=item, qty=qty, source_warehouse=ctx.stores, fg_warehouse=ctx.fg,
				planned_start_date=add_days(nowdate(), -10), expected_delivery_date=add_days(nowdate(), 5),
				description=spec,
			)["name"]
		for nxt in STAGES[1:STAGES.index(stage) + 1]:
			result = manufacturing.advance_stage(wo, notes=STAGE_NOTES[nxt])
			if nxt == "Delivery" and result.get("stock_warning"):
				frappe.throw(f"{wo}: stock entry did not post: {result['stock_warning']}")
		frappe.db.commit()
		chassis = frappe.get_all("Trailer Serial", filters={"work_order": wo}, pluck="chassis_number")
		return wo, chassis

	for item, qty, spec in COMPLETED_BUILDS:
		wo, chassis = build(item, qty, spec, "Delivery")
		serial_register.mark_embossed(work_order=wo)
		print(f"  {wo}  {qty} x {item:16} in stock      {', '.join(chassis)}")

	bto = orders["blue_dune"]
	wo, chassis = build(bto.item, bto.qty, "Built for Blue Dune: company livery, reinforced floor.",
	                    "Delivery", sales_order=bto.name)
	serial_register.mark_embossed(work_order=wo)
	print(f"  {wo}  {bto.qty} x {bto.item:16} built to order {bto.name}  {', '.join(chassis)}")

	for item, qty, stage, spec in IN_PROGRESS_BUILDS:
		wo, chassis = build(item, qty, spec, stage)
		if STAGES.index(stage) >= STAGES.index("Assembly"):
			serial_register.mark_embossed(work_order=wo)
		print(f"  {wo}  {qty} x {item:16} at {stage:11}  {', '.join(chassis)}")


def _selling(ctx, orders):
	def reserve(key):
		o = orders[key]
		free = frappe.get_all("Trailer Serial", filters={"trailer_type": o.item, "status": "In Stock"},
		                      pluck="name", order_by="creation asc", limit=o.qty)
		if len(free) < o.qty:
			frappe.throw(f"Not enough {o.item} in stock for {o.name}")
		for s in free:
			selling.reserve_serial(o.name, s)
		return free

	def deliver(key, serials):
		dn = selling.dispatch_serial(orders[key].name, serials=serials)["name"]
		# Dispatch drafts the order's whole remaining quantity; the clerk opens the
		# draft and sets it to the trailers actually leaving, as the user guide says.
		doc = frappe.get_doc("Delivery Note", dn)
		if doc.items[0].qty != len(serials):
			doc.items[0].qty = len(serials)
			doc.save(ignore_permissions=True)
		selling.submit_delivery_note(dn)
		return dn

	def invoice(dn, submit=True):
		si = selling.invoice_delivery(dn)["name"]
		if submit:
			selling.submit_sales_invoice(si)
		return si

	def receive(si, amount=None):
		pe = selling.receive_against_invoice(si, amount=amount)["name"]
		frappe.get_doc("Payment Entry", pe).submit()
		frappe.db.commit()
		return pe

	def warranty(serials):
		for s in serials:
			frappe.db.set_value("Trailer Serial", s, {
				"warranty_start_date": nowdate(), "warranty_expiry_date": add_days(nowdate(), 730),
			}, update_modified=False)
		frappe.db.commit()

	def chassis(serials):
		return ", ".join(frappe.db.get_value("Trailer Serial", s, "chassis_number") for s in serials)

	# One trailer per delivery note: the note, its invoice and the trailer's status
	# each carry a single chassis number.
	for key, amount_rule in (("gulf_horizon", "full"), ("desert_line", "part"), ("al_noor", None),
	                         ("falcon", "full")):
		serials = reserve(key)
		for s in serials:
			dn = deliver(key, [s])
			si = invoice(dn)
			if amount_rule == "full":
				receive(si)
				paid = "paid"
			elif amount_rule == "part":
				outstanding = flt(frappe.db.get_value("Sales Invoice", si, "outstanding_amount"))
				receive(si, amount=round(outstanding * 0.4, -3))
				paid = "40% received"
			else:
				paid = "unpaid"
			warranty([s])
			print(f"  {orders[key].name}  delivered {dn}, invoiced {si} ({paid})  {chassis([s])}")

	serials = reserve("sandstone")
	dn = deliver("sandstone", serials)
	si = invoice(dn, submit=False)
	warranty(serials)
	print(f"  {orders['sandstone'].name}  delivered {dn}, invoice {si} still draft  {chassis(serials)}")

	serials = reserve("crescent")
	print(f"  {orders['crescent'].name}  reserved, awaiting dispatch  {chassis(serials)}")
	print(f"  {orders['blue_dune'].name}  built to order, awaiting dispatch")
	print(f"  {orders['oasis'].name}  confirmed, no trailer reserved yet")
	print(f"  {orders['rashid'].name}  draft")


def _journals(ctx):
	cash = frappe.db.get_value("Company", ctx.company, "default_cash_account")
	capital = frappe.db.get_value("Account", {"company": ctx.company, "account_name": "Capital Stock",
	                                          "is_group": 0}, "name")
	cc = ctx.cost_center

	def journal(lines, remark, post=True):
		je = selling.create_journal_entry(
			[dict(line, cost_center=cc) for line in lines], user_remark=remark, company=ctx.company,
		)["name"]
		if post:
			selling.post_journal_entry(je)
		print(f"  {je}  {'posted' if post else 'awaiting approval'}  {remark}")

	if capital:
		journal([{"account": cash, "debit": 2500000}, {"account": capital, "credit": 2500000}],
		        "Owners' capital introduced")
	journal([{"account": f"Office Rent - {ctx.abbr}", "debit": 18000}, {"account": cash, "credit": 18000}],
	        "Workshop and office rent, this month")
	journal([{"account": f"Utility Expenses - {ctx.abbr}", "debit": 4350}, {"account": cash, "credit": 4350}],
	        "Electricity and water, fabrication bay")
	journal([{"account": f"Salary - {ctx.abbr}", "debit": 86000},
	         {"account": f"Payroll Payable - {ctx.abbr}", "credit": 86000}],
	        "Salary accrual, production and office staff", post=False)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _ensure(doctype, name, values):
	if frappe.db.exists(doctype, name):
		return name
	doc = frappe.get_doc(dict(values, doctype=doctype))
	doc.insert(ignore_permissions=True)
	return doc.name


def _ensure_tree(doctype, parent, name_field, parent_field, names, leaf=True):
	if not frappe.db.exists(doctype, parent):
		frappe.get_doc({"doctype": doctype, name_field: parent, "is_group": 1}).insert(ignore_permissions=True)
	for name in names:
		if not frappe.db.exists(doctype, name):
			frappe.get_doc({
				"doctype": doctype, name_field: name, parent_field: parent, "is_group": 0 if leaf else 1,
			}).insert(ignore_permissions=True)


def _ensure_item(ctx, code, name, group, uom, trailer=False, valuation_rate=0, reorder=None):
	if frappe.db.exists("Item", code):
		return
	doc = frappe.get_doc({
		"doctype": "Item", "item_code": code, "item_name": name, "description": name,
		"item_group": group, "stock_uom": uom, "is_stock_item": 1,
		"include_item_in_manufacturing": 0 if trailer else 1,
		"valuation_rate": valuation_rate, "custom_is_trailer": 1 if trailer else 0,
		"item_defaults": [{"company": ctx.company, "default_warehouse": ctx.fg if trailer else ctx.stores}],
	})
	if reorder:
		doc.append("reorder_levels", {
			"warehouse": ctx.stores, "warehouse_reorder_level": reorder[0],
			"warehouse_reorder_qty": reorder[1], "material_request_type": "Purchase",
		})
	doc.insert(ignore_permissions=True)


def _ensure_price(item, rate, price_list):
	if frappe.db.exists("Item Price", {"item_code": item, "price_list": price_list}):
		return
	frappe.get_doc({
		"doctype": "Item Price", "item_code": item, "price_list": price_list,
		"price_list_rate": rate, "currency": "AED",
	}).insert(ignore_permissions=True)
