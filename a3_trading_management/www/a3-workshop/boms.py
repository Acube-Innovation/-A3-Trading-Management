from a3_trading_management.website_utils import require_login

no_cache = 1


def get_context(context):
	require_login(context)
	context.title = "BOMs"
	context.page_icon = "fa-sitemap"
	context.subtitle = "What goes into each build, and the operations; the manager approves new ones"
	context.breadcrumb = "BOMs"

	from a3_trading_management.api.supervisor_portal import list_boms, list_operations

	context.boms = list_boms()
	context.operations = list_operations()
	context.drafts = len([b for b in context.boms if b.docstatus == 0])
	context.approved = len([b for b in context.boms if b.docstatus == 1])
	context.page_count = len(context.boms)
	context.page_count_label = "BOMs"
	return context
