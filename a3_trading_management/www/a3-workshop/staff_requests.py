from frappe.utils import add_days, nowdate

from a3_trading_management.website_utils import require_login

no_cache = 1


def get_context(context):
	require_login(context)
	context.title = "Staff Requests"
	context.page_icon = "fa-user-plus"
	context.subtitle = "People the floor needs; HR approves and sets the pay"
	context.breadcrumb = "Staff Requests"

	from a3_trading_management.api.supervisor_portal import list_designations, list_staff_requests

	context.requests = list_staff_requests()
	context.designations = list_designations()
	context.pending = len([r for r in context.requests if r.status == "Pending"])
	context.approved = len([r for r in context.requests if r.status == "Open & Approved"])
	context.default_by = add_days(nowdate(), 21)
	context.page_count = len(context.requests)
	context.page_count_label = "requests"
	return context
