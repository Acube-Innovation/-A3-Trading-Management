"""The trading company's RAK Bank account (13 Nov 2026), printed as BANK DETAILS on
every RLI print (templates/print/rli_bank.html reads it from Bank Account).

Idempotent: creates the Bank and the Bank Account only when missing, and makes this
account the company's default."""

import frappe

COMPANY_ABBR = "RT"
BANK = "RAK BANK"
SWIFT = "NRAKAEAK"
ACCOUNT_NAME = "REDLINES INTERNATIONAL TRADING L.L.C"
ACCOUNT_NO = "0333765227001"
IBAN = "AE660400000333765227001"
LEDGER = "Banks Current Accounts - RT"


def execute():
	company = frappe.db.get_value("Company", {"abbr": COMPANY_ABBR}, "name")
	if not company:
		return
	if not frappe.db.exists("Bank", BANK):
		frappe.get_doc({"doctype": "Bank", "bank_name": BANK, "swift_number": SWIFT}).insert(ignore_permissions=True)
	existing = frappe.db.get_value("Bank Account", {"company": company, "bank_account_no": ACCOUNT_NO}, "name")
	if existing:
		return
	doc = frappe.get_doc({
		"doctype": "Bank Account",
		"account_name": ACCOUNT_NAME,
		"bank": BANK,
		"company": company,
		"is_company_account": 1,
		"is_default": 1,
		"bank_account_no": ACCOUNT_NO,
		"iban": IBAN,
		"account": LEDGER if frappe.db.exists("Account", {"name": LEDGER, "company": company, "is_group": 0}) else None,
	})
	doc.insert(ignore_permissions=True)
