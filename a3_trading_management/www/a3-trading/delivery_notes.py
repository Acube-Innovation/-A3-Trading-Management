import frappe
from a3_trading_management.website_utils import require_login

no_cache = 1


def get_context(context):
	require_login(context)
	context.title = "Delivery Notes"
	context.page_icon = "fa-truck-fast"
	context.subtitle = "Trailers dispatched against their serial, ready to invoice"
	context.breadcrumb = "Delivery Notes"

	from a3_trading_management.api.selling import list_deliveries

	context.deliveries = list_deliveries()
	context.page_count = len(context.deliveries)
	context.page_count_label = "delivery notes"
	return context
