import frappe

from a3_trading_management.website_utils import require_login

no_cache = 1


def get_context(context):
	require_login(context)
	context.title = "General Ledger"
	context.page_icon = "fa-book-open"
	context.subtitle = "Every posting to one account, with the running balance"
	context.breadcrumb = "Ledger"
	fd = frappe.form_dict
	context.account = fd.get("account") or ""
	context.from_date = fd.get("from_date") or ""
	context.to_date = fd.get("to_date") or ""
	# Quick picks: the accounts people look at most.
	context.quick = frappe.get_all("Account", filters={"is_group": 0, "disabled": 0, "account_type": ["in", ["Bank", "Cash", "Receivable", "Payable"]]},
	                               fields=["name", "account_type"], order_by="account_type, name")
	return context
