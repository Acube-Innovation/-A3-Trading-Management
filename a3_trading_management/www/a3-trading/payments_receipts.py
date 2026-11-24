import frappe
from frappe.utils import nowdate

from a3_trading_management.website_utils import require_login

no_cache = 1


def get_context(context):
	require_login(context)
	context.title = "Payments & Receipts"
	context.page_icon = "fa-right-left"
	context.subtitle = "Payment receipts, bank receipts and bank payments: money in and out, set against the invoices it settles"
	context.breadcrumb = "Payments & Receipts"

	from a3_trading_management.api.accounts import list_vouchers, money_accounts
	from a3_trading_management.api.buying import supplier_outstanding
	from a3_trading_management.api.selling import receivables

	context.vouchers = list_vouchers()
	context.receivables = receivables()
	context.payables = supplier_outstanding()
	context.total_in = sum(r["total"] for r in context.receivables)
	context.total_out = sum(p["total"] for p in context.payables)
	context.money_accounts = money_accounts()
	context.modes = frappe.get_all("Mode of Payment", filters={"enabled": 1}, fields=["name", "type"], order_by="name")
	context.today = nowdate()
	context.drafts = len([v for v in context.vouchers if v["docstatus"] == 0])
	context.page_count = len(context.vouchers)
	context.page_count_label = "vouchers"
	return context
