import frappe
from a3_trading_management.website_utils import require_login

no_cache = 1


def get_context(context):
	require_login(context)
	context.title = "Journal Entries"
	context.page_icon = "fa-book"
	context.subtitle = "Manual journals, accruals and adjustments"
	context.breadcrumb = "Journal Entries"

	from a3_trading_management.api.selling import list_journal_entries

	context.entries = list_journal_entries(search=(frappe.form_dict.get("q") or "").strip() or None)
	context.drafts = [e for e in context.entries if e.docstatus == 0]
	context.page_count = len(context.entries)
	context.page_count_label = "journals"
	return context
