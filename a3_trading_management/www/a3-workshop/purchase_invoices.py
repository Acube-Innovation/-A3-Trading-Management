import frappe
from a3_trading_management.website_utils import require_login

no_cache = 1


def get_context(context):
	require_login(context)
	context.title = "Purchase Invoices"
	context.page_icon = "fa-file-invoice"
	context.subtitle = "Bills against receipts and orders, with a hold before the ledger"
	context.breadcrumb = "Purchase Invoices"

	from a3_trading_management.api.buying import list_purchase_invoices

	context.invoices = list_purchase_invoices(search=(frappe.form_dict.get("q") or "").strip() or None)
	context.on_hold = [i for i in context.invoices if i.get("custom_approval_hold")]
	context.page_count = len(context.invoices)
	context.page_count_label = "invoices"
	return context
