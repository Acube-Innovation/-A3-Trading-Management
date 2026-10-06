# Invoice deletion — removes a submitted (or cancelled) Sales or Purchase Invoice
# together with everything it wrote, once an Invoice Deletion Request has been
# approved by Finance and then the Owner.
#
# ERPNext will not delete a submitted invoice, so the order is always:
#   1. checks          — closed / frozen periods and documents that must stay
#   2. dependants      — credit/debit notes, dunning letters, draft assets go first
#   3. unlink payments — payments stay on the party as unallocated advances
#   4. cancel          — ERPNext's own cancel reverses ledger, stock, serials and
#                        puts orders / receipts / delivery notes back to To Bill
#   5. leftovers       — GL, Payment Ledger, Stock Ledger, gain/loss journal and
#                        repost references for THIS invoice only (the company-wide
#                        Accounts Settings switch for this stays off)
#   6. delete          — Frappe keeps a JSON copy in Deleted Document
# It all runs inside the request's own save, so any error rolls everything back.

import frappe
from frappe import _
from frappe.utils import formatdate, getdate

INVOICE_TYPES = ("Sales Invoice", "Purchase Invoice")
REQUEST = "Invoice Deletion Request"
OPEN_STATES = ("Draft", "Pending Finance", "Pending Owner")


def _check_type(doctype):
	if doctype not in INVOICE_TYPES:
		frappe.throw(_("Only Sales and Purchase Invoices can be deleted this way."))


def _parents(child, filters, parenttype=None):
	if parenttype:
		filters = dict(filters, parenttype=parenttype)
	return sorted(set(frappe.get_all(child, filters=filters, pluck="parent")))


def _open(doctype, names):
	"""Those of `names` that are not cancelled."""
	if not names:
		return []
	return frappe.get_all(doctype, filters={"name": ["in", names], "docstatus": ["<", 2]}, pluck="name")


def _count(doctype, filters):
	return frappe.db.count(doctype, filters)


# ---------------------------------------------------------------------------
# Impact — what a deletion would do. Shown when the request is raised and
# re-checked when the Owner approves.
# ---------------------------------------------------------------------------

def get_impact(doctype, name):
	_check_type(doctype)
	if not frappe.db.exists(doctype, name):
		frappe.throw(_("{0} {1} does not exist.").format(doctype, name))
	doc = frappe.get_doc(doctype, name)
	out = {"blockers": [], "removes": [], "updates": [], "warnings": []}
	_impact(doc, out, prefix="")
	# A credit note points at the same orders as its invoice; list each line once.
	return {key: list(dict.fromkeys(lines)) for key, lines in out.items()}


def _impact(doc, out, prefix):
	dt, name = doc.doctype, doc.name
	b, rm, up, warn = out["blockers"], out["removes"], out["updates"], out["warnings"]
	tag = prefix or ""

	if doc.docstatus == 0 and not prefix:
		b.append(_("{0} is a draft. A draft has no ledger entries — delete it directly.").format(name))
		return

	# Closed books: never rewrite a frozen or closed period.
	date = getdate(doc.posting_date)
	frozen = frappe.db.get_single_value("Accounts Settings", "acc_frozen_upto")
	if frozen and date <= getdate(frozen):
		b.append(_("{0}{1} is dated {2}, on or before the Accounts Frozen date {3}.").format(
			"", name, formatdate(date), formatdate(frozen)))
	pcv = frappe.db.get_value("Period Closing Voucher", {"docstatus": 1, "company": doc.company},
	                          "max(period_end_date)")
	if pcv and date <= getdate(pcv):
		b.append(_("{0}{1} falls in books closed up to {2} (Period Closing Voucher).").format(
			"", name, formatdate(pcv)))
	closed = frappe.db.sql(
		"""select ap.name from `tabAccounting Period` ap join `tabClosed Document` cd on cd.parent = ap.name
		where ap.company = %s and cd.closed = 1 and cd.document_type = %s and %s between ap.start_date and ap.end_date""",
		(doc.company, dt, date))
	if closed:
		b.append(_("{0}{1} falls in the closed Accounting Period {2}.").format("", name, closed[0][0]))

	# Credit / debit notes against it are deleted first, through these same checks.
	for r in frappe.get_all(dt, filters={"return_against": name}, pluck="name"):
		label = _("Credit Note") if dt == "Sales Invoice" else _("Debit Note")
		rm.append(_("{0} {1} (against {2}) — cancelled and deleted first").format(label, r, name))
		_impact(frappe.get_doc(dt, r), out, prefix=f"{r}: ")

	if dt == "Sales Invoice":
		if doc.get("is_consolidated"):
			b.append(_("{0}{1} is a consolidated POS invoice — cancel its POS Closing Entry instead.").format("", name))
		for dn in _open("Delivery Note", _parents("Delivery Note Item", {"against_sales_invoice": name})):
			b.append(_("{0}Delivery Note {1} was made from {2}. Cancel the delivery note first — "
			           "it moved real stock.").format("", dn, name))
		for idisc in _open("Invoice Discounting", _parents("Discounted Invoice", {"sales_invoice": name})):
			b.append(_("{0}{1} is in Invoice Discounting {2}. Cancel that first.").format("", name, idisc))
		for dun in _parents("Overdue Payment", {"sales_invoice": name}, "Dunning"):
			rm.append(_("Dunning {0} — cancelled and deleted").format(dun))
		for se in frappe.get_all("Stock Entry", filters={"sales_invoice_no": name}, pluck="name"):
			up.append(_("Stock Entry {0} — reference to {1} cleared").format(se, name))
		sources = [("Sales Order", "sales_order"), ("Delivery Note", "delivery_note")]
	else:
		for a in frappe.get_all("Asset", filters={"purchase_invoice": name}, fields=["name", "docstatus"]):
			if a.docstatus == 1:
				b.append(_("{0}Asset {1} was created from {2} and is submitted. Cancel the asset first.").format(
					"", a.name, name))
			else:
				rm.append(_("Asset {0} ({1}) — deleted").format(a.name, _("draft") if a.docstatus == 0 else _("cancelled")))
		for lcv in _open("Landed Cost Voucher", _parents(
				"Landed Cost Purchase Receipt", {"receipt_document_type": dt, "receipt_document": name})):
			b.append(_("{0}Landed Cost Voucher {1} adds cost to {2}. Cancel it first.").format("", lcv, name))
		for pr in _parents("Purchase Receipt Item", {"purchase_invoice": name}):
			up.append(_("Purchase Receipt {0} — link to {1} cleared").format(pr, name))
		sources = [("Purchase Order", "purchase_order"), ("Purchase Receipt", "purchase_receipt")]

	for preq in frappe.get_all("Payment Request", filters={"reference_doctype": dt, "reference_name": name},
	                           fields=["name", "docstatus"]):
		if preq.docstatus == 1:
			b.append(_("{0}Payment Request {1} is submitted against {2}. Cancel it first.").format("", preq.name, name))
		else:
			rm.append(_("Payment Request {0} — deleted").format(preq.name))

	# Payments stay, unallocated, on the customer / supplier.
	for pe in _open("Payment Entry", _parents("Payment Entry Reference",
	                                          {"reference_doctype": dt, "reference_name": name})):
		up.append(_("Payment Entry {0} — unlinked from {1}; stays as an unallocated advance").format(pe, name))
	for je in _open("Journal Entry", _parents("Journal Entry Account",
	                                          {"reference_type": dt, "reference_name": name})):
		up.append(_("Journal Entry {0} — unlinked from {1}").format(je, name))

	# Where it came from: billing rolls back on cancel.
	if doc.docstatus == 1:
		for src_dt, field in sources:
			for src in sorted({row.get(field) for row in doc.items if row.get(field)}):
				up.append(_("{0} {1} — billed % rolled back (To Bill again)").format(src_dt, src))

	for amended in frappe.get_all(dt, filters={"amended_from": name}, pluck="name"):
		up.append(_("{0} {1} — its 'Amended From' link to {2} cleared").format(dt, amended, name))

	# What the invoice wrote.
	gl = _count("GL Entry", {"voucher_type": dt, "voucher_no": name})
	ple = _count("Payment Ledger Entry", {"voucher_type": dt, "voucher_no": name})
	sle = _count("Stock Ledger Entry", {"voucher_type": dt, "voucher_no": name})
	if doc.docstatus == 1:
		rm.append(_("{0}{1} — cancelled (ERPNext reverses its entries), then deleted").format("", name))
	else:
		rm.append(_("{0}{1} — already cancelled; deleted").format("", name))
	if gl:
		rm.append(_("{0}General Ledger: {1} entries (plus their reversals)").format(tag, gl))
	if ple:
		rm.append(_("{0}Payment Ledger: {1} entries").format(tag, ple))
	if sle:
		rm.append(_("{0}Stock Ledger: {1} entries (plus their reversals); stock put back").format(tag, sle))
	bundles = _count("Serial and Batch Bundle", {"voucher_type": dt, "voucher_no": name})
	if bundles:
		rm.append(_("{0}Serial and Batch Bundles: {1}").format(tag, bundles))
	for riv in frappe.get_all("Repost Item Valuation", filters={"voucher_type": dt, "voucher_no": name},
	                          fields=["name", "status", "docstatus"]):
		if riv.status == "In Progress":
			b.append(_("{0}Stock revaluation {1} for {2} is running now. Try again in a few minutes.").format(
				"", riv.name, name))

	if doc.docstatus == 1 and doc.get("update_stock"):
		warn.append(_("{0}{1} moved stock. If those items were sold or used since, cancelling fails with "
		              "a negative-stock message and nothing is deleted.").format("", name))
	if doc.docstatus == 1 and doc.outstanding_amount != doc.grand_total and not doc.get("is_return"):
		warn.append(_("{0}{1} is partly or fully paid. The payment is kept as an advance on the party "
		              "and must be re-allocated or refunded.").format("", name))


def impact_text(impact):
	parts = []
	for key, title in (("blockers", _("BLOCKED — fix these first")), ("removes", _("Will be removed")),
	                   ("updates", _("Will be updated")), ("warnings", _("Please note"))):
		if impact.get(key):
			parts.append(title + ":\n" + "\n".join("  • " + line for line in impact[key]))
	return "\n\n".join(parts)


# ---------------------------------------------------------------------------
# Deletion — only ever called from an approved Invoice Deletion Request.
# ---------------------------------------------------------------------------

def delete_invoice(doctype, name):
	"""Delete the invoice and everything it wrote. Returns a log of what was done."""
	impact = get_impact(doctype, name)
	if impact["blockers"]:
		frappe.throw("<br>".join(impact["blockers"]), title=_("Cannot delete {0}").format(name))
	log = []
	_delete_one(doctype, name, log)
	return log


def _cancel_and_delete(doctype, name, log):
	doc = frappe.get_doc(doctype, name)
	if doc.docstatus == 1:
		doc.flags.ignore_permissions = True
		doc.cancel()
	frappe.delete_doc(doctype, name, ignore_permissions=True, force=True)
	log.append(_("Deleted {0} {1}").format(doctype, name))


def _delete_one(doctype, name, log):
	from erpnext.accounts.utils import delete_exchange_gain_loss_journal, unlink_ref_doc_from_payment_entries

	doc = frappe.get_doc(doctype, name)

	# 2. Dependants first.
	for r in frappe.get_all(doctype, filters={"return_against": name}, pluck="name"):
		_delete_one(doctype, r, log)
	if doctype == "Sales Invoice":
		for dun in _parents("Overdue Payment", {"sales_invoice": name}, "Dunning"):
			_cancel_and_delete("Dunning", dun, log)
	else:
		for asset in frappe.get_all("Asset", filters={"purchase_invoice": name, "docstatus": ["!=", 1]}, pluck="name"):
			frappe.delete_doc("Asset", asset, ignore_permissions=True, force=True)
			log.append(_("Deleted Asset {0}").format(asset))
	for preq in frappe.get_all("Payment Request", filters={
			"reference_doctype": doctype, "reference_name": name, "docstatus": ["!=", 1]}, pluck="name"):
		frappe.delete_doc("Payment Request", preq, ignore_permissions=True, force=True)
		log.append(_("Deleted Payment Request {0}").format(preq))

	# 3 + 4. Unlink payments (they stay as advances), then ERPNext's own cancel.
	stock_pairs = frappe.get_all("Stock Ledger Entry", filters={"voucher_type": doctype, "voucher_no": name},
	                             fields=["item_code", "warehouse"], distinct=True)
	if doc.docstatus == 1:
		payments = _open("Payment Entry", _parents("Payment Entry Reference",
		                                           {"reference_doctype": doctype, "reference_name": name}))
		unlink_ref_doc_from_payment_entries(doc)
		for pe in payments:
			log.append(_("Unlinked Payment Entry {0} — now an unallocated advance").format(pe))
		doc.reload()
		doc.flags.ignore_permissions = True
		doc.cancel()
		log.append(_("Cancelled {0} {1} (ledger and stock reversed, source documents back to To Bill)").format(
			doctype, name))

	# 5. Leftovers for this invoice only.
	_detach_reposts(doctype, name, stock_pairs, log)
	delete_exchange_gain_loss_journal(doc)
	counts = {}
	for ledger in ("GL Entry", "Payment Ledger Entry", "Stock Ledger Entry"):
		counts[ledger] = _count(ledger, {"voucher_type": doctype, "voucher_no": name})
		frappe.db.delete(ledger, {"voucher_type": doctype, "voucher_no": name})
	delinked = {"against_voucher_type": doctype, "against_voucher_no": name, "delinked": 1}
	counts["Payment Ledger Entry"] += _count("Payment Ledger Entry", delinked)
	frappe.db.delete("Payment Ledger Entry", delinked)
	doc._remove_advance_payment_ledger_entries()
	log.append(_("Removed {0} GL, {1} Payment Ledger and {2} Stock Ledger entries of {3}").format(
		counts["GL Entry"], counts["Payment Ledger Entry"], counts["Stock Ledger Entry"], name))

	# Pointers that would otherwise block the delete or dangle after it.
	for amended in frappe.get_all(doctype, filters={"amended_from": name}, pluck="name"):
		frappe.db.set_value(doctype, amended, "amended_from", None, update_modified=False)
		log.append(_("Cleared 'Amended From' on {0}").format(amended))
	if doctype == "Sales Invoice":
		frappe.db.sql("update `tabStock Entry` set sales_invoice_no = null where sales_invoice_no = %s", name)
		frappe.db.sql("""update `tabDelivery Note Item` set against_sales_invoice = null, si_detail = null
			where against_sales_invoice = %s and docstatus = 2""", name)
	else:
		frappe.db.sql("update `tabPurchase Receipt Item` set purchase_invoice = null, purchase_invoice_item = null "
		              "where purchase_invoice = %s", name)

	# 6. The invoice itself (Frappe keeps a copy in Deleted Document).
	frappe.delete_doc(doctype, name, ignore_permissions=True)
	log.append(_("Deleted {0} {1}").format(doctype, name))


def _detach_reposts(doctype, name, stock_pairs, log):
	"""Repost Item Valuation rows point at the invoice and would block its delete.

	A finished repost is history: its link is cleared. A queued one (the cancel
	usually queues one for back-dated stock) still has to run, so it is re-pointed
	at each item + warehouse the invoice touched, which needs no voucher."""
	rivs = frappe.get_all("Repost Item Valuation", filters={"voucher_type": doctype, "voucher_no": name},
	                      fields=["name", "status", "docstatus", "posting_date", "posting_time", "company"])
	for riv in rivs:
		if riv.status == "Queued" and riv.docstatus == 1 and stock_pairs:
			first, rest = stock_pairs[0], stock_pairs[1:]
			frappe.db.set_value("Repost Item Valuation", riv.name, {
				"based_on": "Item and Warehouse", "item_code": first.item_code, "warehouse": first.warehouse,
				"voucher_type": None, "voucher_no": None}, update_modified=False)
			for pair in rest:
				frappe.get_doc({
					"doctype": "Repost Item Valuation", "based_on": "Item and Warehouse",
					"item_code": pair.item_code, "warehouse": pair.warehouse, "company": riv.company,
					"posting_date": riv.posting_date, "posting_time": riv.posting_time,
				}).submit()
			log.append(_("Stock revaluation {0} re-pointed to the items it covers").format(riv.name))
		else:
			frappe.db.set_value("Repost Item Valuation", riv.name, {"voucher_type": None, "voucher_no": None},
			                    update_modified=False)


# ---------------------------------------------------------------------------
# Whitelisted entry points used by the Desk form and the portal pages.
# ---------------------------------------------------------------------------

def open_request(doctype, name):
	return frappe.db.get_value(REQUEST, {"invoice_type": doctype, "invoice": name,
	                                      "workflow_state": ["in", OPEN_STATES]}, ["name", "workflow_state"],
	                           as_dict=True)


def portal_context(doctype):
	"""For the portal invoice lists: may this user raise a request, and which
	invoices already have one open (invoice -> state)."""
	can = frappe.has_permission(REQUEST, "create")
	rows = frappe.get_all(REQUEST, filters={"invoice_type": doctype, "workflow_state": ["in", OPEN_STATES]},
	                      fields=["invoice", "workflow_state"], ignore_permissions=True)
	return can, {r.invoice: r.workflow_state for r in rows}


@frappe.whitelist()
def preview_deletion(doctype, name):
	_check_type(doctype)
	frappe.has_permission(doctype, "read", doc=name, throw=True)
	impact = get_impact(doctype, name)
	impact["open_request"] = open_request(doctype, name)
	return impact


@frappe.whitelist()
def request_deletion(doctype, name, reason):
	"""Raise a deletion request and send it straight to Finance."""
	from frappe.model.workflow import apply_workflow

	_check_type(doctype)
	frappe.has_permission(doctype, "read", doc=name, throw=True)
	frappe.has_permission(REQUEST, "create", throw=True)
	req = frappe.get_doc({"doctype": REQUEST, "invoice_type": doctype, "invoice": name, "reason": reason})
	req.insert()
	req = apply_workflow(req, "Submit for Approval")
	return {"name": req.name, "workflow_state": req.workflow_state}
