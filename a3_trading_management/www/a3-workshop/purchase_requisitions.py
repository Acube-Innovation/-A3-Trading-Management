import frappe
from a3_trading_management.website_utils import require_login

no_cache = 1


def get_context(context):
	require_login(context)
	context.title = "Purchase Requisitions"
	context.page_icon = "fa-clipboard-check"
	context.subtitle = "Requests for material, wherever they come from"
	context.breadcrumb = "Purchase Requisitions"

	from a3_trading_management.api.buying import list_requisitions, list_suppliers

	context.search = (frappe.form_dict.get("q") or "").strip()
	context.requisitions = list_requisitions(search=context.search or None)
	# Only compliant suppliers are offered for conversion — the block would refuse
	# the order anyway, so offering the rest would just produce a dead end.
	context.suppliers = [s for s in list_suppliers() if not s["compliance"]["blocked"]]
	context.page_count = len(context.requisitions)
	context.page_count_label = "requisitions"
	return context
