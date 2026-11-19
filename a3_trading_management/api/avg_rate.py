"""The average purchase rate per stock unit, and BOM costs that follow it.

Rule (Redlines, 16 Nov 2026): an item's rate is everything ever paid for it
divided by everything ever received, over every posted purchase invoice.
10 pcs for 100, then 2 boxes of 10 for 100 -> 200 / 30 = 6.67 a piece.

The rate is kept as an Item Price in the buying price list AVERAGE_PRICE_LIST,
in the item's stock unit, and every BOM costs its materials from that list
(rm_cost_as_per = "Price List"). Posting or cancelling a bill recomputes the
rate of its items and re-costs every BOM that uses them, approved ones and
their parent BOMs included.

This is a costing rate only. Stock valuation in the ledger stays FIFO.
"""

import frappe
from frappe.utils import flt

AVERAGE_PRICE_LIST = "RLI Average Purchase"


def ensure_price_list():
	if frappe.db.exists("Price List", AVERAGE_PRICE_LIST):
		return AVERAGE_PRICE_LIST
	company = frappe.defaults.get_global_default("company") or frappe.db.get_value("Company", {}, "name")
	currency = (company and frappe.get_cached_value("Company", company, "default_currency")) or "AED"
	frappe.get_doc({
		"doctype": "Price List",
		"price_list_name": AVERAGE_PRICE_LIST,
		"currency": currency,
		"buying": 1,
		"selling": 0,
		"enabled": 1,
	}).insert(ignore_permissions=True)
	return AVERAGE_PRICE_LIST


def average_rates(item_codes=None):
	"""{item_code: (amount, stock qty)} over posted, non-return purchase invoices."""
	condition, values = "", {}
	if item_codes is not None:
		if not item_codes:
			return {}
		condition = "and pii.item_code in %(items)s"
		values["items"] = tuple(item_codes)
	data = frappe.db.sql(
		f"""select pii.item_code, sum(pii.base_net_amount) as amount, sum(pii.stock_qty) as qty
		from `tabPurchase Invoice Item` pii
		join `tabPurchase Invoice` pi on pi.name = pii.parent
		where pi.docstatus = 1 and pi.is_return = 0 and pii.item_code is not null {condition}
		group by pii.item_code""",
		values, as_dict=True,
	)
	return {d.item_code: (flt(d.amount), flt(d.qty)) for d in data}


def average_rate(item_code):
	"""The average purchase rate per stock unit, or None when never bought."""
	amount, qty = average_rates([item_code]).get(item_code, (0, 0))
	return amount / qty if qty > 0 else None


def _set_item_price(item_code, rate):
	stock_uom = frappe.get_cached_value("Item", item_code, "stock_uom")
	existing = frappe.db.get_value(
		"Item Price", {"price_list": AVERAGE_PRICE_LIST, "item_code": item_code}, "name"
	)
	if existing:
		frappe.db.set_value("Item Price", existing, {"price_list_rate": rate, "uom": stock_uom})
		return
	frappe.get_doc({
		"doctype": "Item Price",
		"price_list": AVERAGE_PRICE_LIST,
		"item_code": item_code,
		"uom": stock_uom,
		"price_list_rate": rate,
	}).insert(ignore_permissions=True)


def _use_average(bom):
	"""Point a BOM at the average price list (allowed on an approved BOM too)."""
	if bom.rm_cost_as_per == "Price List" and bom.buying_price_list == AVERAGE_PRICE_LIST:
		return
	bom.rm_cost_as_per = "Price List"
	bom.buying_price_list = AVERAGE_PRICE_LIST
	bom.price_list_currency = frappe.db.get_value("Price List", AVERAGE_PRICE_LIST, "currency")
	bom.plc_conversion_rate = 1
	bom.db_set({
		"rm_cost_as_per": bom.rm_cost_as_per, "buying_price_list": bom.buying_price_list,
		"price_list_currency": bom.price_list_currency, "plc_conversion_rate": 1,
	}, update_modified=False)


def price_unbilled(item_codes):
	"""Give an item that was never bought on a bill its stock valuation rate as its
	price, so a BOM costs it the way the portal BOM form estimates it, not at 0
	(19 Nov 2026: discs received as stock showed 29.42 on the form, 0 on the BOM)."""
	from erpnext.manufacturing.doctype.bom.bom import get_valuation_rate

	item_codes = {i for i in item_codes if i}
	if not item_codes:
		return
	priced = set(frappe.get_all(
		"Item Price", filters={"price_list": AVERAGE_PRICE_LIST, "item_code": ["in", list(item_codes)]},
		pluck="item_code",
	))
	company = frappe.defaults.get_global_default("company") or frappe.db.get_value("Company", {}, "name")
	for item_code in item_codes - priced:
		if not frappe.db.exists("Item", item_code):
			continue
		rate = flt(get_valuation_rate({"item_code": item_code, "company": company}))
		if rate:
			_set_item_price(item_code, rate)


def bom_before_validate(doc, method=None):
	"""BOM before_validate: price unbilled materials before ERPNext costs the BOM."""
	if doc.rm_cost_as_per == "Price List" and doc.buying_price_list == AVERAGE_PRICE_LIST:
		price_unbilled([d.item_code for d in doc.items])


def recost_boms(item_codes):
	"""Re-cost every draft or approved BOM holding one of these items."""
	if not item_codes:
		return []
	boms = frappe.db.sql_list(
		"""select distinct parent from `tabBOM Item`
		where parenttype = 'BOM' and item_code in %s and docstatus < 2""",
		(tuple(item_codes),),
	)
	for name in boms:
		bom = frappe.get_doc("BOM", name)
		_use_average(bom)
		price_unbilled([d.item_code for d in bom.items])
		# Rewrites row rates, totals, and (if the cost moved) every parent BOM.
		bom.update_cost(update_parent=True, from_child_bom=True, update_hour_rate=False, save=True)
		# ERPNext writes a row back only when its rate changed, so a row whose
		# amount moved for another reason (rounding) kept its old amount.
		for row in bom.items:
			row.db_update()
	return boms


def refresh_items(item_codes):
	"""Recompute the average of these items and push it into their BOMs."""
	ensure_price_list()
	item_codes = sorted({i for i in item_codes if i})
	totals = average_rates(item_codes)
	for item_code in item_codes:
		amount, qty = totals.get(item_code, (0, 0))
		if qty > 0:
			_set_item_price(item_code, amount / qty)
		else:
			# No posted bill left (the only one was cancelled): drop the price.
			frappe.db.delete("Item Price", {"price_list": AVERAGE_PRICE_LIST, "item_code": item_code})
	return recost_boms(item_codes)


def on_purchase_invoice_change(doc, method=None):
	"""Purchase Invoice on_submit / on_cancel."""
	if doc.get("is_return"):
		return
	refresh_items([row.item_code for row in doc.items])


def rebuild_all():
	"""Backfill: an average for every item ever billed, every BOM on the list.

	An item in a BOM that was never billed keeps its current valuation rate as its
	price, so moving the BOM onto the list does not drop its cost to zero."""
	from erpnext.manufacturing.doctype.bom.bom import get_valuation_rate

	ensure_price_list()
	totals = average_rates()
	for item_code, (amount, qty) in totals.items():
		if qty > 0 and frappe.db.exists("Item", item_code):
			_set_item_price(item_code, amount / qty)

	bom_items = frappe.db.sql_list(
		"select distinct item_code from `tabBOM Item` where parenttype = 'BOM' and docstatus < 2"
	)
	for item_code in bom_items:
		if item_code in totals or not frappe.db.exists("Item", item_code):
			continue
		company = frappe.defaults.get_global_default("company")
		rate = flt(get_valuation_rate({"item_code": item_code, "company": company}))
		if rate:
			_set_item_price(item_code, rate)
	return recost_boms(bom_items)

