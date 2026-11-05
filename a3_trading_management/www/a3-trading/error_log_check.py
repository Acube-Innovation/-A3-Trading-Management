import frappe
from a3_trading_management.website_utils import require_login

no_cache = 1


def get_context(context):
	require_login(context)
	context.title = "Error Log Check"
	context.page_icon = "fa-stethoscope"
	context.subtitle = "Is client-error capture working on this site?"
	context.breadcrumb = "Error Log Check"

	# Rendered server-side as well as fetched by the page, so this still answers
	# the question on a site where the JS itself is the thing that is broken.
	from a3_trading_management.api.error_log import intake_status

	context.status = intake_status()
	return context
