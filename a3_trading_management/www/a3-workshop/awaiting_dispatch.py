import frappe
from a3_trading_management.website_utils import require_login

no_cache = 1


def get_context(context):
	require_login(context)
	context.title = "Awaiting Dispatch"
	context.page_icon = "fa-hourglass-half"
	context.subtitle = "Confirmed orders not yet delivered — dispatch a trailer against its serial"
	context.breadcrumb = "Awaiting Dispatch"

	from a3_trading_management.api.selling import list_sales_orders

	# Confirmed orders not yet fully dispatched — what the yard is working through.
	context.to_dispatch = [o for o in list_sales_orders()
	                       if o.docstatus == 1 and (o.per_delivered or 0) < 100]
	context.page_count = len(context.to_dispatch)
	context.page_count_label = "to dispatch"
	return context
