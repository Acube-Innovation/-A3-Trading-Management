import frappe
from a3_trading_management.website_utils import require_login

no_cache = 1


def get_context(context):
	require_login(context)
	context.title = "Purchase Orders"
	context.page_icon = "fa-file-contract"
	context.subtitle = "Orders against requisitions, and what has been received"
	context.breadcrumb = "Purchase Orders"

	from a3_trading_management.api.buying import list_purchase_orders

	context.search = (frappe.form_dict.get("q") or "").strip()
	# ?material_request= narrows the list to the orders made from one requisition.
	context.material_request = (frappe.form_dict.get("material_request") or "").strip()
	context.orders = list_purchase_orders(search=context.search or None, material_request=context.material_request or None)
	context.page_count = len(context.orders)
	context.page_count_label = "orders"
	return context
