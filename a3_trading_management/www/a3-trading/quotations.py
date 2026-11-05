from a3_trading_management.website_utils import require_login

no_cache = 1


def get_context(context):
	require_login(context)
	context.title = "Quotations"
	context.page_icon = "fa-file-lines"
	context.subtitle = "Quotations drafted on the floor; Sales adds the prices and sends them"
	context.breadcrumb = "Quotations"

	from a3_trading_management.api.supervisor_portal import list_quotations

	context.quotations = list_quotations()
	context.drafts = len([q for q in context.quotations if q.docstatus == 0])
	context.sent = len([q for q in context.quotations if q.docstatus == 1])
	context.page_count = len(context.quotations)
	context.page_count_label = "quotations"
	return context
