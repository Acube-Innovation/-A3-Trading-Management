"""One-time UAE VAT setup from the "UAE VAT Tax Setup and Ledger Guide" (15 Oct 2026),
plus the back-fills the VAT 201 report needs on existing invoices.

Site data, so a backup restore undoes it. Safe to run again:

    bench --site a3_trading execute a3_trading_management.integrations.uae_vat_setup.apply

Pass dry_run=1 (``--kwargs "{'dry_run': 1}"``) to only list what would change.
"""

import frappe
from frappe.utils import flt

from a3_trading_management.integrations.uae_vat import purchase_vat


def apply(dry_run=0):
	log = []
	for company, abbr in frappe.get_all(
		"Company", filters={"country": "United Arab Emirates"}, fields=["name", "abbr"], as_list=True
	):
		_setup_company(company, abbr, log, dry_run)
	_backfill_invoices(log, dry_run)
	if dry_run:
		frappe.db.rollback()
	else:
		frappe.db.commit()
	print("\n".join(log) or "Nothing to change.")
	return log


def _setup_company(company, abbr, log, dry_run):
	vat5 = f"VAT 5% - {abbr}"
	excise = [a for a in (f"Excise 50% - {abbr}", f"Excise 100% - {abbr}") if frappe.db.exists("Account", a)]

	# Drafts using an excise template would fail to save once it is disabled.
	for dt in ("Purchase Invoice", "Sales Invoice"):
		child = "Purchase Taxes and Charges" if dt == "Purchase Invoice" else "Sales Taxes and Charges"
		drafts = frappe.db.sql_list(
			f"""select distinct p.name from `tab{dt}` p
			left join `tab{child}` t on t.parent = p.name and t.parenttype = %(dt)s
			left join `tab{dt} Item` i on i.parent = p.name
			where p.docstatus = 0 and p.company = %(co)s
			and (t.account_head in %(ex)s or i.item_tax_template like 'UAE Excise%%')""",
			{"dt": dt, "co": company, "ex": excise or [""]},
		)
		for name in drafts:
			if dry_run:
				log.append(f"{dt} {name} (draft): excise replaced by UAE VAT 5% - {abbr}")
				continue
			frappe.db.savepoint("retax")
			try:
				_retax_draft(dt, name, f"UAE VAT 5% - {abbr}")
				log.append(f"{dt} {name} (draft): excise replaced by UAE VAT 5% - {abbr}")
			except Exception as e:
				frappe.db.rollback(save_point="retax")
				frappe.clear_messages()
				log.append(f"FIX BY HAND {dt} {name} (draft): uses excise, could not re-save ({e})")

	# 1. Zero / Exempted item templates must zero the 5% row too.
	for name in (f"UAE VAT Zero - {abbr}", f"UAE VAT Exempted - {abbr}"):
		if not frappe.db.exists("Item Tax Template", name):
			continue
		doc = frappe.get_doc("Item Tax Template", name)
		row = next((r for r in doc.taxes if r.tax_type == vat5), None)
		if row and flt(row.tax_rate) == 0:
			continue
		log.append(f"Item Tax Template {name}: {vat5} at 0%")
		if row:
			row.tax_rate = 0
		else:
			doc.append("taxes", {"tax_type": vat5, "tax_rate": 0})
		doc.save()

	# 3. UAE VAT Settings: VAT accounts only.
	settings = frappe.get_doc("UAE VAT Settings", company)
	keep = [r for r in settings.uae_vat_accounts if r.account not in excise]
	if len(keep) != len(settings.uae_vat_accounts):
		log.append(f"UAE VAT Settings {company}: removed {', '.join(excise)}")
		settings.uae_vat_accounts = keep
		settings.save()

	# 4. Excise is not used: disable its templates and accounts.
	for dt in ("Sales Taxes and Charges Template", "Purchase Taxes and Charges Template", "Item Tax Template"):
		for name in frappe.get_all(dt, filters={"company": company, "disabled": 0, "name": ["like", "UAE Excise%"]}, pluck="name"):
			log.append(f"{dt} {name}: disabled")
			frappe.db.set_value(dt, name, "disabled", 1)
	for account in excise:
		if not frappe.db.get_value("Account", account, "disabled"):
			log.append(f"Account {account}: disabled")
			frappe.db.set_value("Account", account, "disabled", 1)

	# 5. Settlement account.
	duties = frappe.db.get_value("Account", vat5, "parent_account")
	_account(company, "VAT Payable to FTA", duties, 0, log)

	# 6. Non-recoverable VAT: an expense, so it never reaches box 9.
	expense_parent = next(
		(a for a in (f"Indirect Expenses - {abbr}", f"Office & Various Expenses - {abbr}") if frappe.db.exists("Account", a)),
		None,
	)
	if expense_parent:
		nonrec = _account(company, "VAT Non-Recoverable", expense_parent, 5, log)
		title = "UAE VAT 5% Non-Recoverable"
		if not frappe.db.exists("Purchase Taxes and Charges Template", f"{title} - {abbr}"):
			log.append(f"Purchase Taxes and Charges Template {title} - {abbr}: created")
			frappe.get_doc({
				"doctype": "Purchase Taxes and Charges Template",
				"title": title,
				"company": company,
				"taxes": [{
					"category": "Total",
					"add_deduct_tax": "Add",
					"charge_type": "On Net Total",
					"account_head": nonrec,
					"rate": 5,
					"description": "VAT 5% (non-recoverable)",
				}],
			}).insert()
	else:
		log.append(f"SKIPPED VAT Non-Recoverable for {company}: no Indirect Expenses group")

	# 2. Items already ticked: the item hook adds a template row per Tax Category.
	for item in frappe.get_all("Item", filters=[["is_zero_rated", "=", 1]], pluck="name") + frappe.get_all(
		"Item", filters=[["is_exempt", "=", 1]], pluck="name"
	):
		doc = frappe.get_doc("Item", item)
		before = len(doc.taxes)
		from a3_trading_management.integrations.uae_vat import item_validate

		item_validate(doc)
		if len(doc.taxes) != before:
			log.append(f"Item {item}: zero/exempt template added for every Tax Category")
			doc.save()


def _account(company, account_name, parent, rate, log):
	abbr = frappe.get_cached_value("Company", company, "abbr")
	name = f"{account_name} - {abbr}"
	if not frappe.db.exists("Account", name):
		log.append(f"Account {name}: created under {parent}")
		frappe.get_doc({
			"doctype": "Account",
			"account_name": account_name,
			"parent_account": parent,
			"company": company,
			"account_type": "Tax",
			"tax_rate": rate,
			"account_currency": frappe.get_cached_value("Company", company, "default_currency"),
		}).insert()
	return name


def _retax_draft(doctype, name, template):
	from erpnext.controllers.accounts_controller import get_taxes_and_charges

	doc = frappe.get_doc(doctype, name)
	doc.set_posting_time = 1  # keep the draft's own date; saving would move it to today
	child ="Purchase Taxes and Charges Template" if doctype == "Purchase Invoice" else "Sales Taxes and Charges Template"
	for item in doc.items:
		if (item.item_tax_template or "").startswith("UAE Excise"):
			item.item_tax_template = None
			item.item_tax_rate = "{}"
	doc.taxes_and_charges = template
	doc.set("taxes", get_taxes_and_charges(child, template))
	if doctype == "Purchase Invoice":
		doc.recoverable_standard_rated_expenses = 0  # recomputed by the validate hook
	doc.save()


def _backfill_invoices(log, dry_run):
	"""Fields the VAT 201 report reads but that submitted invoices never got
	(they predate the hooks, or were wiped by a restore). Neither field is
	allow-on-submit, so set them directly; nothing else on the invoice changes."""
	uae = frappe.get_all("Company", filters={"country": "United Arab Emirates"}, pluck="name")
	if not uae:
		return

	for name in frappe.get_all(
		"Purchase Invoice",
		filters={"docstatus": 1, "company": ["in", uae], "reverse_charge": ["!=", "Y"],
			"recoverable_standard_rated_expenses": 0},
		pluck="name",
	):
		vat = purchase_vat(frappe.get_doc("Purchase Invoice", name))
		if vat:
			log.append(f"Purchase Invoice {name}: Recoverable Standard Rated Expenses = {vat:,.2f}")
			if not dry_run:
				frappe.db.set_value("Purchase Invoice", name, "recoverable_standard_rated_expenses", vat, update_modified=False)

	from frappe.contacts.doctype.address.address import get_company_address

	for si in frappe.get_all(
		"Sales Invoice",
		filters={"docstatus": ["<", 2], "company": ["in", uae], "vat_emirate": ["in", ["", None]]},
		fields=["name", "company", "company_address", "docstatus"],
	):
		address = si.company_address or get_company_address(si.company).company_address
		emirate = address and frappe.db.get_value("Address", address, "emirate")
		if not emirate:
			log.append(f"Sales Invoice {si.name}: NO VAT Emirate - company address has no Emirate")
			continue
		log.append(f"Sales Invoice {si.name}: VAT Emirate = {emirate}")
		if not dry_run:
			frappe.db.set_value("Sales Invoice", si.name,
				{"vat_emirate": emirate, "company_address": address}, update_modified=False)
