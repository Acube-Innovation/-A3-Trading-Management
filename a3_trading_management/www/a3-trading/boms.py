import frappe

from a3_trading_management.website_utils import require_login

no_cache = 1


def get_context(context):
	require_login(context)
	context.title = "BOMs"
	context.page_icon = "fa-sitemap"
	context.subtitle = "What goes into each build, the operations and what it costs"
	context.breadcrumb = "BOMs"

	from a3_trading_management.api.supervisor_portal import list_boms, list_operations

	context.boms = list_boms()
	context.operations = list_operations()
	context.can_submit = bool(frappe.has_permission("BOM", "submit"))
	# The Supervisor sees no costs: not hidden on the page, never sent to it.
	if context.get("supervisor"):
		for b in context.boms:
			b.pop("total_cost", None)
		context.hour_rates = {}
	else:
		context.hour_rates = dict(frappe.get_all("Workstation", fields=["name", "hour_rate"], as_list=True))
	# ?new_bom=<item>&return=<url>: New Work Order sent the user here for an item
	# with no BOM; the form opens on that item and saving goes back there.
	item = frappe.form_dict.get("new_bom")
	context.new_bom = (
		{"item": item, "item_name": frappe.db.get_value("Item", item, "item_name") or item}
		if item and frappe.db.exists("Item", item) else None
	)
	back = frappe.form_dict.get("return") or ""
	context.bom_return = back if back.startswith("/a3-trading/") else ""
	context.drafts = len([b for b in context.boms if b.docstatus == 0])
	context.approved = len([b for b in context.boms if b.docstatus == 1])
	context.page_count = len(context.boms)
	context.page_count_label = "BOMs"
	return context
