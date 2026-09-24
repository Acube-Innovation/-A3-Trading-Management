import frappe
from a3_trading_management.website_utils import require_login

no_cache = 1


def get_context(context):
	require_login(context)
	context.title = "Delivery & Sales Invoice"
	context.page_icon = "fa-truck-fast"
	context.subtitle = "Dispatch a trailer against its serial, and invoice it"
	context.breadcrumb = "Delivery & Sales Invoice"

	from a3_trading_management.api.selling import (
		list_deliveries, list_sales_invoices, list_sales_orders,
	)

	context.deliveries = list_deliveries()
	context.invoices = list_sales_invoices()
	# Confirmed orders not yet fully dispatched — what the yard is working through.
	context.to_dispatch = [o for o in list_sales_orders()
	                       if o.docstatus == 1 and (o.per_delivered or 0) < 100]
	# The delete button only appears for someone who may actually delete one.
	context.can_delete_invoice = frappe.has_permission("Sales Invoice", "delete")
	context.page_count = len(context.deliveries) + len(context.invoices)
	context.page_count_label = "documents"
	return context
