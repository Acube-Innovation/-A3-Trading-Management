import frappe
from a3_trading_management.website_utils import require_login

no_cache = 1


def get_context(context):
	require_login(context)
	context.title = "Sales Invoices"
	context.page_icon = "fa-file-invoice"
	context.subtitle = "Invoices raised from deliveries, and what is still to be received"
	context.breadcrumb = "Sales Invoices"

	from a3_trading_management.api.selling import list_sales_invoices

	context.invoices = list_sales_invoices()
	# The delete button only appears for someone who may actually delete one.
	context.can_delete_invoice = frappe.has_permission("Sales Invoice", "delete")
	# Posted invoices are deleted through an approved Invoice Deletion Request.
	from a3_trading_management.api.invoice_delete import portal_context

	context.can_request_delete, context.open_deletions = portal_context("Sales Invoice")
	context.page_count = len(context.invoices)
	context.page_count_label = "invoices"
	return context
