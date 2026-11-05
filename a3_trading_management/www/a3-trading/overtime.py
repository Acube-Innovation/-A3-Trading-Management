import frappe
from frappe.utils import get_first_day, getdate, nowdate

from a3_trading_management.website_utils import require_login

no_cache = 1


def get_context(context):
	require_login(context)
	context.title = "Overtime"
	context.page_icon = "fa-clock"
	context.subtitle = "Overtime worked on the floor, entered by the Supervisor"
	context.breadcrumb = "Overtime"

	from a3_trading_management.api.supervisor_portal import list_overtime

	context.entries = list_overtime()
	month_start = get_first_day(nowdate())
	this_month = [e for e in context.entries if e.docstatus == 1 and getdate(e.overtime_date) >= month_start]
	context.month_entries = len(this_month)
	context.month_hours = sum(e.hours or 0 for e in this_month)
	context.drafts = len([e for e in context.entries if e.docstatus == 0])
	context.work_orders = frappe.get_all("Work Order", filters={"docstatus": ["<", 2], "status": ["not in", ["Completed", "Stopped", "Closed"]]},
	                                     fields=["name", "item_name"], order_by="modified desc", limit_page_length=50)
	context.today = nowdate()
	context.page_count = len(context.entries)
	context.page_count_label = "entries"
	return context
