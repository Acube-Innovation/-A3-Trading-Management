"""Fill the fields the UAE VAT 201 report reads, which ERPNext leaves blank.

The report does not read the ledger. Sales boxes 1a-1g group posted Sales
Invoices by `vat_emirate` (ERPNext only fetches it from a company address picked
on the form), and box 9 sums `recoverable_standard_rated_expenses`, which ERPNext
expects to be typed on every Purchase Invoice. Invoices raised from the portal
or in code therefore never reached the report.
"""

import frappe
from frappe import _
from frappe.utils import flt


def _is_uae(company):
	return frappe.get_cached_value("Company", company, "country") == "United Arab Emirates"


def _vat_accounts(company):
	return set(frappe.get_all("UAE VAT Account", filters={"parent": company}, pluck="account"))


def purchase_vat(doc):
	"""Input VAT on the bill: its tax rows booked to a UAE VAT account."""
	accounts = _vat_accounts(doc.company)
	total = 0.0
	for tax in doc.get("taxes") or []:
		if tax.account_head in accounts and tax.get("add_deduct_tax", "Add") == "Add":
			total += flt(tax.base_tax_amount_after_discount_amount)
	return total


def sales_invoice_validate(doc, method=None):
	"""VAT Emirate from the invoice's company address, or the company's default one."""
	if not _is_uae(doc.company) or doc.get("vat_emirate") or not doc.meta.has_field("vat_emirate"):
		return
	if not doc.get("company_address"):
		from frappe.contacts.doctype.address.address import get_company_address

		doc.company_address = get_company_address(doc.company).company_address
	if doc.company_address:
		doc.vat_emirate = frappe.db.get_value("Address", doc.company_address, "emirate") or ""


def purchase_invoice_before_validate(doc, method=None):
	"""Reverse charge bills claim through Recoverable Reverse Charge, and ERPNext's
	own validate refuses one that still carries the standard-rated figure (which
	an earlier save filled in), so clear it before that check runs."""
	if doc.get("reverse_charge") == "Y" and doc.meta.has_field("recoverable_standard_rated_expenses"):
		doc.recoverable_standard_rated_expenses = 0


def purchase_invoice_validate(doc, method=None):
	"""Recoverable Standard Rated Expenses = the bill's input VAT, unless someone
	typed their own figure. A figure that still equals the VAT computed at the
	last save counts as automatic and follows the taxes when they change."""
	if (not _is_uae(doc.company) or doc.get("reverse_charge") == "Y"
			or not doc.meta.has_field("recoverable_standard_rated_expenses")):
		return
	current = flt(doc.recoverable_standard_rated_expenses)
	before = doc.get_doc_before_save()
	was_auto = before is not None and current == flt(purchase_vat(before))
	# A debit note arrives with the original bill's positive figure copied over;
	# its VAT is negative, so a positive figure on a return is never typed.
	copied = doc.get("is_return") and current > 0
	if not current or was_auto or copied:
		doc.recoverable_standard_rated_expenses = purchase_vat(doc)


def purchase_invoice_before_submit(doc, method=None):
	"""Refuse a bill that would put a wrong figure in box 9 (or the stock ledger).

	Box 9 counts every posted bill carrying recoverable VAT, so a test or duplicate
	bill goes straight into the return. ACC-PINV-2026-00010 was one: no supplier
	invoice number, and a stock item billed without a receipt or Update Stock, which
	debited Stock Received But Not Billed for goods that never arrived.
	"""
	if doc.get("is_return"):
		return

	if _is_uae(doc.company) and not doc.get("reverse_charge") == "Y":
		missing = [label for field, label in (("bill_no", _("Supplier Invoice No")),
			("bill_date", _("Supplier Invoice Date"))) if not doc.get(field)]
		if missing:
			frappe.throw(_("Enter the {0} from the supplier's tax invoice before posting {1}. "
				"Input VAT can only be claimed against a supplier tax invoice.").format(
				_(" and ").join(missing), doc.name), title=_("Supplier Invoice Missing"))

	if doc.get("bill_no"):
		duplicate = frappe.db.get_value("Purchase Invoice", {
			"supplier": doc.supplier, "bill_no": doc.bill_no.strip(), "docstatus": 1,
			"is_return": 0, "name": ("!=", doc.name),
		})
		if duplicate:
			frappe.throw(_("Supplier invoice {0} from {1} is already posted as {2}. "
				"Posting it again would claim its VAT twice.").format(
				frappe.bold(doc.bill_no), doc.supplier, frappe.bold(duplicate)),
				title=_("Duplicate Supplier Invoice"))

	if not doc.get("update_stock"):
		unreceived = [row.item_code for row in doc.items
			if not (row.get("purchase_receipt") or row.get("purchase_order")) and row.item_code
			and frappe.get_cached_value("Item", row.item_code, "is_stock_item")]
		if unreceived:
			frappe.throw(_("{0}: stock item(s) {1} are not linked to a Purchase Receipt or Order. "
				"Tick Update Stock to receive them on this invoice, or bill against the receipt.").format(
				doc.name, ", ".join(frappe.bold(i) for i in unreceived)),
				title=_("Goods Not Received"))


def _uae_vat_templates():
	"""{company: {"standard": ..., "zero": ..., "exempt": ...}} for each UAE company,
	using the Item Tax Templates ERPNext's UAE setup creates ("UAE VAT Zero - RT")."""
	out = {}
	for company, abbr in frappe.get_all(
		"Company", filters={"country": "United Arab Emirates"}, fields=["name", "abbr"], as_list=True
	):
		names = {
			"standard": f"UAE VAT 5% - {abbr}",
			"zero": f"UAE VAT Zero - {abbr}",
			"exempt": f"UAE VAT Exempted - {abbr}",
		}
		out[company] = {k: v for k, v in names.items() if frappe.db.exists("Item Tax Template", v)}
	return out


def item_validate(doc, method=None):
	"""Make Is Zero Rated / Is Exempt actually change the VAT charged.

	ERPNext treats those ticks as reporting flags only: the invoice's 5% row still
	applies unless the item carries a 0% Item Tax Template. So a ticked item gets
	the matching UAE template (replacing any other UAE VAT one), and an unticked
	item loses the Zero/Exempted template this put there.

	ERPNext only uses an item tax row whose Tax Category equals the invoice's
	(blank matches blank), so the template goes in once per Tax Category.
	"""
	if not (doc.meta.has_field("is_zero_rated") and doc.meta.has_field("is_exempt")):
		return
	kind = "zero" if doc.is_zero_rated else "exempt" if doc.is_exempt else None
	categories = ["", *frappe.get_all("Tax Category", filters={"disabled": 0}, pluck="name")]

	for templates in _uae_vat_templates().values():
		wanted = templates.get(kind) if kind else None
		# Flagged: no other UAE VAT template may compete. Unflagged: only drop ours.
		drop = set(templates.values()) if wanted else {templates.get("zero"), templates.get("exempt")}
		drop.discard(wanted)
		drop.discard(None)

		for row in [r for r in doc.get("taxes") or [] if r.item_tax_template in drop]:
			doc.remove(row)
		if not wanted:
			continue
		have = {r.tax_category or "" for r in doc.get("taxes") or [] if r.item_tax_template == wanted}
		for category in categories:
			if category not in have:
				doc.append("taxes", {"item_tax_template": wanted, "tax_category": category})
