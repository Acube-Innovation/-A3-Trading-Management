import frappe
from frappe.utils import getdate, nowdate

from a3_trading_management.website_utils import require_login

no_cache = 1


def get_context(context):
	require_login(context)
	from a3_trading_management.api.accounts import receivable_summary

	fd = frappe.form_dict
	context.party_type = "Supplier" if fd.get("type") == "Supplier" else "Customer"
	is_ar = context.party_type == "Customer"
	context.title = "Accounts Receivable" if is_ar else "Accounts Payable"
	context.page_icon = "fa-hand-holding-dollar"
	context.subtitle = ("What each customer owes, by how late it is" if is_ar
	                    else "What we owe each supplier, by how late it is")
	context.breadcrumb = context.title
	context.report_date = str(getdate(fd.get("as_on") or nowdate()))
	context.data = receivable_summary(context.party_type, context.report_date)
	context.page_count = len(context.data["parties"])
	context.page_count_label = "customers" if is_ar else "suppliers"
	return context
