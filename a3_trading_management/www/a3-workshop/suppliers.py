import frappe
from a3_trading_management.website_utils import require_login

no_cache = 1


def get_context(context):
	require_login(context)
	context.title = "Suppliers"
	context.page_icon = "fa-truck-field"
	context.subtitle = "Supplier list and compliance position"
	context.breadcrumb = "Suppliers"

	from a3_trading_management.api.buying import list_suppliers

	context.search = (frappe.form_dict.get("q") or "").strip()
	context.suppliers = list_suppliers(search=context.search or None)
	context.blocked = [s for s in context.suppliers if s["compliance"]["blocked"]]
	context.expiring = [s for s in context.suppliers if s["compliance"]["expiring_soon"]
	                    and not s["compliance"]["blocked"]]
	context.page_count = len(context.suppliers)
	context.page_count_label = "suppliers"
	return context
