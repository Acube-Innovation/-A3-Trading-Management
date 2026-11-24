import frappe
from frappe.utils import nowdate

from a3_trading_management.website_utils import require_login

no_cache = 1


def get_context(context):
	require_login(context)
	context.title = "Credit & Debit Notes"
	context.page_icon = "fa-file-circle-minus"
	context.subtitle = "Credit notes to customers and debit notes to suppliers, against the invoice they correct"
	context.breadcrumb = "Credit & Debit Notes"

	from a3_trading_management.api.accounts import list_notes

	context.notes = list_notes()
	context.today = nowdate()
	context.page_count = len(context.notes)
	context.page_count_label = "notes"
	return context
