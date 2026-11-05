import frappe
from a3_trading_management.website_utils import require_login

no_cache = 1


def get_context(context):
	require_login(context)
	context.title = "Manufacturing"
	context.page_icon = "fa-industry"
	context.subtitle = "Manufacture items to stock — work order to delivery"
	context.breadcrumb = "Manufacturing"
	# ?wo=<name> opens with that order selected (Home, Serial Register); ?embed=1
	# shows only the Production Detail panel, for the pop-up on the register.
	context.embed = 1 if frappe.form_dict.get("embed") else 0
	# The page is data-driven entirely through a3_trading_management.api.manufacturing.*
	# (see the <script> in manufacturing.html). Nothing is prefetched here so the
	# list, KPI cards and detail panel always reflect live Work Order data.
	return context
