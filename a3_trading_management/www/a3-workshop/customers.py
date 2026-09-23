import frappe
from a3_trading_management.website_utils import require_login

no_cache = 1


def get_context(context):
	require_login(context)
	context.title = "Customers"
	context.page_icon = "fa-users"
	context.subtitle = "Customer profiles"
	context.breadcrumb = "Customers"

	# One read-only call builds the whole grid: Customers with their confirmed
	# orders, billed total and contact details.
	from a3_trading_management.api.customers import list_customer_cards

	# ?q= filters server-side.
	context.search = (frappe.form_dict.get("q") or "").strip()
	context.customers = list_customer_cards(search=context.search or None, limit=0)

	# Topbar total, plus the master count so a search never hides how many
	# customers exist.
	context.page_count = len(context.customers)
	context.page_count_label = "matching" if context.search else "customers"
	context.total_customers = frappe.db.count("Customer")
	return context
