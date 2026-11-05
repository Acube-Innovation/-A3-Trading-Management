import frappe
from a3_trading_management.website_utils import require_login

no_cache = 1


def get_context(context):
	require_login(context)
	context.title = "Quality"
	context.page_icon = "fa-clipboard-check"
	context.subtitle = "What is waiting for inspection, and every inspection recorded"
	context.breadcrumb = "Quality"

	from a3_trading_management.api.quality import get_board

	context.board = get_board()
	context.tab = frappe.form_dict.get("tab") or "pending"
	context.page_count = context.board["stats"]["waiting"]
	context.page_count_label = "to inspect"
	return context
