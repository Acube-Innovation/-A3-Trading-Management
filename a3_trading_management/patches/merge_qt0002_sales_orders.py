"""25 Nov 2026 (user): quotation RLI-TR-QT-0002 (DP World, 75 trailers plus 75
transportation) was turned into eight Sales Orders of one trailer each,
SAL-ORD-2026-00006 to -00013, each with its own work order, delivery note and
serial. They become one order: SAL-ORD-2026-00006 for the whole quotation --
75 x STO-ITEM-2026-00043 at 58,000 and 75 x STO-ITEM-2026-00146 (TRANSPORTATION)
at 1,000 -- and the other seven are deleted.

The eight work orders, delivery notes and serials stay as they are (several work
orders and notes for one order is how it is meant to work); they now point at
SAL-ORD-2026-00006 and its trailer line, which shows 8 of 75 delivered.

A Sales Order posts nothing to the ledger, so nothing is re-posted. The patch
only runs while the data is exactly as found (all eight submitted, one trailer
each, none invoiced); otherwise it leaves everything alone."""

import frappe
from frappe.utils import flt

QUOTATION = "RLI-TR-QT-0002"
KEEP = "SAL-ORD-2026-00006"
MERGE = ["SAL-ORD-2026-%05d" % n for n in range(7, 14)]
TRAILER = "STO-ITEM-2026-00043"
TRANSPORT = "STO-ITEM-2026-00146"


def _as_found():
	names = [KEEP] + MERGE
	rows = frappe.db.sql("""select so.name, so.docstatus, so.customer, so.per_billed, soi.item_code, soi.qty,
			soi.prevdoc_docname, (select count(*) from `tabSales Order Item` x where x.parent = so.name) n
		from `tabSales Order` so join `tabSales Order Item` soi on soi.parent = so.name
		where so.name in %s""", [names], as_dict=True)
	if len(rows) != len(names):
		return False
	customers = {r.customer for r in rows}
	return len(customers) == 1 and all(
		r.docstatus == 1 and r.n == 1 and r.item_code == TRAILER and flt(r.qty) == 1
		and r.prevdoc_docname == QUOTATION and not flt(r.per_billed) for r in rows
	) and not frappe.db.exists("Sales Invoice Item", {"sales_order": ["in", names]})


def execute():
	if not _as_found():
		return

	keep = frappe.get_doc("Sales Order", KEEP)
	trailer = keep.items[0]
	quote = {q.item_code: q for q in frappe.get_all("Quotation Item", filters={"parent": QUOTATION},
		fields=["name", "item_code", "item_name", "description", "uom", "stock_uom", "conversion_factor",
		        "qty", "rate", "warehouse", "item_group", "brand"])}

	# 1. The kept order carries the whole quotation.
	q_trailer, q_transport = quote[TRAILER], quote[TRANSPORT]
	trailer.qty = flt(q_trailer.qty)
	trailer.stock_qty = trailer.qty * flt(trailer.conversion_factor or 1)
	transport = keep.append("items", {
		"item_code": TRANSPORT, "item_name": q_transport.item_name, "description": q_transport.description,
		"item_group": q_transport.item_group, "brand": q_transport.brand,
		"uom": q_transport.uom, "stock_uom": q_transport.stock_uom,
		"conversion_factor": flt(q_transport.conversion_factor) or 1,
		"qty": flt(q_transport.qty), "stock_qty": flt(q_transport.qty) * (flt(q_transport.conversion_factor) or 1),
		"rate": flt(q_transport.rate), "price_list_rate": flt(q_transport.rate),
		"warehouse": q_transport.warehouse or trailer.warehouse,
		"delivery_date": trailer.delivery_date or keep.delivery_date,
		"prevdoc_docname": QUOTATION, "quotation_item": q_transport.name,
	})
	keep.calculate_taxes_and_totals()
	keep.set_total_in_words()
	trailer.db_update()
	# A line added to a submitted order is submitted too, or ERPNext does not count it.
	transport.docstatus = 1
	transport.db_insert()
	for tax in keep.taxes:
		tax.db_update()
	# One instalment, for the new total.
	for row in keep.payment_schedule:
		share = flt(row.invoice_portion) / 100 if len(keep.payment_schedule) > 1 else 1
		row.payment_amount = flt(keep.grand_total * share, row.precision("payment_amount"))
		row.base_payment_amount = flt(keep.base_grand_total * share, row.precision("base_payment_amount"))
		row.outstanding = row.payment_amount
		row.db_update()
	keep.db_update()

	# 2. Everything that pointed at the seven orders points at the kept one.
	for so in MERGE:
		frappe.db.sql("""update `tabDelivery Note Item` set against_sales_order = %s, so_detail = %s
			where against_sales_order = %s""", (KEEP, trailer.name, so))
		frappe.db.sql("""update `tabWork Order` set sales_order = %s, sales_order_item = %s
			where sales_order = %s""", (KEEP, trailer.name, so))
		frappe.db.sql("update `tabTrailer Serial` set sales_order = %s where sales_order = %s", (KEEP, so))
	frappe.db.sql("""update `tabWork Order` set sales_order_item = %s
		where sales_order = %s and production_item = %s and ifnull(sales_order_item, '') = ''""",
		(trailer.name, KEEP, TRAILER))

	# 3. The seven orders go (nothing links to them any more).
	for so in MERGE:
		frappe.db.set_value("Sales Order", so, "docstatus", 2, update_modified=False)
		frappe.db.sql("update `tabSales Order Item` set docstatus = 2 where parent = %s", so)
		frappe.delete_doc("Sales Order", so, force=1, ignore_permissions=True, delete_permanently=True)

	# 4. Delivered 8 of 75: let ERPNext's own status updater count it from the notes.
	dn = frappe.db.get_value("Delivery Note Item", {"against_sales_order": KEEP, "docstatus": 1}, "parent")
	if dn:
		frappe.get_doc("Delivery Note", dn).update_prevdoc_status()
	keep = frappe.get_doc("Sales Order", KEEP)
	keep.set_status(update=True, update_modified=False)

	# 5. Stock reserved for the order, and the quotation's status.
	from erpnext.stock.stock_balance import get_reserved_qty, update_bin_qty

	for row in keep.items:
		if row.warehouse and frappe.get_cached_value("Item", row.item_code, "is_stock_item"):
			update_bin_qty(row.item_code, row.warehouse, {"reserved_qty": get_reserved_qty(row.item_code, row.warehouse)})
	# The quotation's ordered quantities, recounted from the order as submit does it.
	from erpnext.controllers.status_updater import StatusUpdater

	StatusUpdater.update_prevdoc_status(keep)
	keep.update_prevdoc_status("submit")

	keep.add_comment("Info", "Merged from {0} (one trailer each, raised from {1}) into one order for the whole quotation: "
	                 "75 trailers and 75 transportation. Their work orders, delivery notes and serials now point here."
	                 .format(", ".join(MERGE), QUOTATION))
