import frappe
from a3_trading_management.website_utils import require_login

no_cache = 1


def get_context(context):
	require_login(context)
	context.title = "Supplier Payments"
	context.page_icon = "fa-money-bill-transfer"
	context.subtitle = "Pay against invoices, and what is outstanding by age"
	context.breadcrumb = "Supplier Payments"

	from a3_trading_management.api.buying import list_supplier_payments, supplier_outstanding

	context.payments = list_supplier_payments(search=(frappe.form_dict.get("q") or "").strip() or None)
	context.outstanding = supplier_outstanding()
	context.total_outstanding = sum(o["total"] for o in context.outstanding)
	context.page_count = len(context.payments)
	context.page_count_label = "payments"
	return context
