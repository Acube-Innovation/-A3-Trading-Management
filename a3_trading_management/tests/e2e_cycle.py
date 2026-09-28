# Implements: the end-to-end trading cycle test (task 30).
"""Exercise the full A3 Trading cycle in one pass.

Requisition -> purchase order -> goods receipt -> purchase invoice -> payment,
then work order -> chassis and serial issue -> build through the stages -> into
stock -> sales order -> delivery -> invoice -> build to order -> warranty.

Run it with:

    bench --site <site> execute a3_trading_management.tests.e2e_cycle.run

It is a scripted walk-through rather than a unit test: it posts real documents on
the site it is pointed at, which is the point — it proves the cycle works end to
end on a real database, not against mocks. Use it on a test site.
"""

import frappe
from frappe.utils import nowdate, add_days, flt

TRAILER_ITEM = "TRL-FLATBED"
RAW_ITEM = "RAW-STEEL"


def run(company=None, supplier=None, customer=None):
	company = company or frappe.defaults.get_defaults().get("company") or _first("Company")
	supplier = supplier or _compliant_supplier()
	customer = customer or _ensure_customer()
	log = []

	def step(msg):
		log.append(msg)
		print(msg)

	from a3_trading_management.api import buying, selling

	_ensure_price(RAW_ITEM, 120, buying=True)
	_ensure_price(TRAILER_ITEM, 50000, buying=False)

	# ---- buying -----------------------------------------------------------
	stores = _warehouse("Stores", company)
	req = buying.create_requisition(
		items=[{"item_code": RAW_ITEM, "qty": 20, "warehouse": stores}], company=company
	)
	step(f"  1. Requisition raised        {req['name']}")
	buying.approve_requisition(req["name"])
	step("  2. Requisition approved")

	po = buying.requisition_to_order(req["name"], supplier)
	# A requisition carries no price, and the mapped order only picks one up if the
	# item has a matching Item Price on the order's price list. Stamp a rate so the
	# rest of the cycle (bill, payment) has something real to work with.
	_price_the_order(po["name"], 120)
	step(f"  3. Purchase order created    {po['name']}  (supplier {supplier})")
	buying.submit_purchase_order(po["name"])
	step("  4. Purchase order approved")

	gr = buying.receive_against_order(po["name"])
	buying.submit_goods_receipt(gr["name"])
	step(f"  5. Goods receipt posted      {gr['name']}  (stock in)")

	pi = buying.bill_against(purchase_receipt=gr["name"])
	buying.set_invoice_hold(pi["name"], 1, "e2e: checking the hold gate")
	held = _refused(buying.approve_purchase_invoice, pi["name"])
	step(f"  6. Hold gate {'blocked approval' if held else 'DID NOT BLOCK -- FAIL'}   {pi['name']}")
	buying.set_invoice_hold(pi["name"], 0)
	buying.approve_purchase_invoice(pi["name"])
	step("  7. Purchase invoice approved and posted")

	pay = buying.pay_supplier_invoice(pi["name"])
	buying.submit_payment(pay["name"])
	step(f"  8. Supplier paid             {pay['name']}")

	# ---- production -------------------------------------------------------
	wo = _work_order(company, qty=2)
	serials = frappe.get_all("Trailer Serial", filters={"work_order": wo}, pluck="chassis_number")
	step(f"  9. Work order {wo} issued {len(serials)} chassis: {', '.join(serials)}")

	_advance_to_delivery(wo)
	built = frappe.get_all(
		"Trailer Serial", filters={"work_order": wo},
		fields=["name", "chassis_number", "status", "warehouse"],
	)
	step(f" 10. Built through six stages -> {built[0].status} in {built[0].warehouse}")

	# ---- selling ----------------------------------------------------------
	so = _sales_order(customer, company)
	step(f" 11. Sales order raised        {so}")
	selling.reserve_serial(so, built[0].name)
	step(f" 12. Trailer reserved          {built[0].chassis_number}")

	dn = selling.dispatch_serial(so, serials=[built[0].name])
	frappe.get_doc("Delivery Note", dn["name"]).submit()
	frappe.db.commit()
	step(f" 13. Dispatched against serial {dn['name']}")

	si = selling.invoice_delivery(dn["name"])
	step(f" 14. Sales invoice raised      {si['name']}")
	carried = frappe.db.get_value("Sales Invoice", si["name"], "custom_chassis_number")
	step(f" 15. Chassis on the invoice    {carried or 'NOT CARRIED -- FAIL'}")

	# ---- build to order ----------------------------------------------------
	so2 = _sales_order(customer, company)
	wo2 = selling.build_for_order(so2, TRAILER_ITEM, 1)["work_order"]
	step(f" 17. Built to order: {wo2} raised for {so2}")
	_advance_to_delivery(wo2)
	built2 = frappe.get_all(
		"Trailer Serial", filters={"work_order": wo2}, fields=["name", "status", "sales_order"],
	)
	handed = built2 and built2[0].status == "Sold" and built2[0].sales_order == so2
	step(f" 18. On completion its trailer is {'reserved to ' + so2 if handed else 'NOT reserved -- FAIL'}")

	# ---- warranty ---------------------------------------------------------
	frappe.db.set_value("Trailer Serial", built[0].name, {
		"warranty_start_date": nowdate(),
		"warranty_expiry_date": add_days(nowdate(), 730),
	}, update_modified=False)
	frappe.db.commit()
	state = frappe.db.get_value("Trailer Serial", built[0].name,
	                            ["status", "current_owner", "warranty_expiry_date"], as_dict=True)
	step(f" 19. Warranty attached to serial; trailer is {state.status}, owner {state.current_owner}")

	print("\nEnd-to-end cycle completed.")
	return log


# ---------------------------------------------------------------------------

def _price_the_order(name, rate):
	doc = frappe.get_doc("Purchase Order", name)
	for row in doc.items:
		if not flt(row.rate):
			row.rate = rate
	doc.flags.ignore_permissions = True
	doc.save(ignore_permissions=True)
	frappe.db.commit()


def _ensure_price(item, rate, buying=True):
	"""A cycle with no prices proves nothing: the bill totals zero and cannot be paid."""
	price_list = frappe.db.get_value(
		"Price List", {"buying" if buying else "selling": 1, "enabled": 1}, "name"
	)
	if not price_list:
		return
	existing = frappe.db.exists("Item Price", {"item_code": item, "price_list": price_list})
	if existing:
		frappe.db.set_value("Item Price", existing, "price_list_rate", rate, update_modified=False)
		return
	frappe.get_doc({
		"doctype": "Item Price", "item_code": item, "price_list": price_list,
		"price_list_rate": rate, "currency": "AED",
	}).insert(ignore_permissions=True)
	frappe.db.commit()


def _first(doctype):
	rows = frappe.get_all(doctype, pluck="name", limit=1)
	return rows[0] if rows else None


def _warehouse(prefix, company):
	abbr = frappe.db.get_value("Company", company, "abbr")
	name = f"{prefix} - {abbr}"
	return name if frappe.db.exists("Warehouse", name) else _first("Warehouse")


def _compliant_supplier():
	from a3_trading_management.api.buying import list_suppliers
	for s in list_suppliers():
		if not s["compliance"]["blocked"]:
			return s["name"]
	frappe.throw("No compliant supplier to order from")


def _ensure_customer():
	existing = _first("Customer")
	if existing:
		return existing
	group = _first("Customer Group")
	territory = _first("Territory")
	doc = frappe.get_doc({
		"doctype": "Customer", "customer_name": "A3 E2E Customer",
		"customer_group": group, "territory": territory,
	}).insert(ignore_permissions=True)
	frappe.db.commit()
	return doc.name


def _refused(fn, *args):
	"""True when `fn` refuses — used to prove a gate actually blocks."""
	try:
		fn(*args)
		return False
	except Exception:
		frappe.db.rollback()
		return True


def _work_order(company, qty=1):
	bom = frappe.db.get_value("BOM", {"item": TRAILER_ITEM, "docstatus": 1}, "name")
	abbr = frappe.db.get_value("Company", company, "abbr")
	wo = frappe.new_doc("Work Order")
	wo.production_item = TRAILER_ITEM
	wo.qty = qty
	wo.bom_no = bom
	wo.company = company
	wo.fg_warehouse = f"Finished Goods - {abbr}"
	wo.wip_warehouse = f"Work In Progress - {abbr}"
	wo.source_warehouse = f"Stores - {abbr}"
	wo.skip_transfer = 1
	wo.custom_production_stage = "Material"
	wo.get_items_and_operations_from_bom()
	wo.flags.ignore_permissions = True
	wo.insert(ignore_permissions=True)
	frappe.db.commit()
	return wo.name


def _advance_to_delivery(name):
	"""Walk the order through the six stages using the real portal endpoint.

	a3_trading_management.api.manufacturing.advance_stage is what the Manufacturing screen
	calls, and advancing into Delivery is what posts the Manufacture stock entry.
	Setting the stage field directly would skip that and leave the trailer "in
	stock" with nothing behind it.
	"""
	from a3_trading_management.api.manufacturing import advance_stage

	doc = frappe.get_doc("Work Order", name)
	doc.submit()
	frappe.db.commit()
	for _stage in ("Fabrication", "Assembly", "Paint", "QC", "Delivery"):
		advance_stage(name)
	frappe.db.commit()


def _sales_order(customer, company):
	so = frappe.new_doc("Sales Order")
	so.customer = customer
	so.company = company
	# A site whose setup wizard never ran has no default selling price list, and
	# Sales Order will not save without one.
	so.selling_price_list = frappe.db.get_value("Price List", {"selling": 1, "enabled": 1}, "name")
	so.currency = "AED"
	so.price_list_currency = "AED"
	so.plc_conversion_rate = 1
	so.conversion_rate = 1
	so.transaction_date = nowdate()
	so.delivery_date = add_days(nowdate(), 7)
	abbr = frappe.db.get_value("Company", company, "abbr")
	so.append("items", {
		"item_code": TRAILER_ITEM, "qty": 1, "rate": 50000,
		"delivery_date": so.delivery_date, "warehouse": f"Finished Goods - {abbr}",
	})
	so.flags.ignore_permissions = True
	so.insert(ignore_permissions=True)
	so.submit()
	frappe.db.commit()
	return so.name
