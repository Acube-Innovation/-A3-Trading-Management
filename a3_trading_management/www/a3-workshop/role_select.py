import frappe
from a3_trading_management.website_utils import require_login

no_cache = 1


def get_context(context):
	require_login(context)
	context.title = "Select Your Role"
	context.page_icon = "fa-user-shield"
	context.subtitle = "Choose how you want to access the system"
	context.breadcrumb = "Select Your Role"
	return context
