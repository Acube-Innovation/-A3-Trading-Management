"""Fill the fields the UAE VAT 201 report reads, which ERPNext leaves blank.

The report does not read the ledger. Sales boxes 1a-1g group posted Sales
Invoices by `vat_emirate` (ERPNext only fetches it from a company address picked
on the form), and box 9 sums `recoverable_standard_rated_expenses`, which ERPNext
expects to be typed on every Purchase Invoice. Invoices raised from the portal
or in code therefore never reached the report.
"""

import frappe
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
	if not current or was_auto:
		doc.recoverable_standard_rated_expenses = purchase_vat(doc)


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
	"""
	if not (doc.meta.has_field("is_zero_rated") and doc.meta.has_field("is_exempt")):
		return
	kind = "zero" if doc.is_zero_rated else "exempt" if doc.is_exempt else None

	for templates in _uae_vat_templates().values():
		wanted = templates.get(kind) if kind else None
		# Flagged: no other UAE VAT template may compete. Unflagged: only drop ours.
		drop = set(templates.values()) if wanted else {templates.get("zero"), templates.get("exempt")}
		drop.discard(wanted)
		drop.discard(None)

		for row in [r for r in doc.get("taxes") or [] if r.item_tax_template in drop]:
			doc.remove(row)
		if wanted and not any(r.item_tax_template == wanted for r in doc.get("taxes") or []):
			doc.append("taxes", {"item_tax_template": wanted})
