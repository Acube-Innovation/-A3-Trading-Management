from a3_trading_management.website_utils import require_login

no_cache = 1


def get_context(context):
	require_login(context)
	context.title = "Quotations"
	context.page_icon = "fa-file-lines"
	context.breadcrumb = "Quotations"

	if context.get("supervisor"):
		# The floor drafts what the customer wants; no prices on this side.
		from a3_trading_management.api.supervisor_portal import list_quotations

		context.subtitle = "Quotations drafted on the floor; Sales adds the prices and sends them"
		context.quotations = list_quotations()
	else:
		from a3_trading_management.api.selling import list_quotations

		context.subtitle = "Price, approve and turn quotations into sales orders"
		context.quotations = list_quotations()
		context.unpriced = len([q for q in context.quotations if q.get("unpriced")])

	context.drafts = len([q for q in context.quotations if q.docstatus == 0])
	context.approved = len([q for q in context.quotations if q.docstatus == 1])
	context.page_count = len(context.quotations)
	context.page_count_label = "quotations"
	return context
