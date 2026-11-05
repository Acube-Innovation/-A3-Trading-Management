import frappe
from a3_trading_management.website_utils import require_login

no_cache = 1


def get_context(context):
	require_login(context)
	context.title = "Payments & Receipts"
	context.page_icon = "fa-right-left"
	context.subtitle = "Money in and money out, allocated against what it settles"
	context.breadcrumb = "Payments & Receipts"

	from a3_trading_management.api.selling import list_payments, receivables
	from a3_trading_management.api.buying import supplier_outstanding

	context.payments = list_payments(search=(frappe.form_dict.get("q") or "").strip() or None)
	context.receivables = receivables()
	context.payables = supplier_outstanding()
	context.total_in = sum(r["total"] for r in context.receivables)
	context.total_out = sum(p["total"] for p in context.payables)
	context.page_count = len(context.payments)
	context.page_count_label = "entries"
	return context
