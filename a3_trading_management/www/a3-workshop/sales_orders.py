import frappe
from a3_trading_management.website_utils import require_login

no_cache = 1


def get_context(context):
	require_login(context)
	context.title = "Sales Orders"
	context.page_icon = "fa-cart-shopping"
	context.subtitle = "Customer orders for trailers and parts"
	context.breadcrumb = "Sales Orders"

	from a3_trading_management.api.selling import list_sales_orders, available_serials

	context.orders = list_sales_orders(search=(frappe.form_dict.get("q") or "").strip() or None)
	# Finished trailers free to be reserved against an order.
	context.available = available_serials()
	context.page_count = len(context.orders)
	context.page_count_label = "orders"
	return context
