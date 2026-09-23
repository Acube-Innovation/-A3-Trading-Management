import frappe
from a3_trading_management.website_utils import require_login

no_cache = 1


def get_context(context):
	require_login(context)
	context.title = "Serial & Chassis Register"
	context.page_icon = "fa-barcode"
	context.subtitle = "Every trailer serial A3 has ever issued"
	context.breadcrumb = "Serial & Chassis Register"

	# Plain filtered read over Trailer Serial — see
	# a3_trading_management.api.serial_register for why nothing is computed here.
	from a3_trading_management.api.serial_register import list_serials, get_summary

	context.rows = list_serials(limit=500)
	context.summary = get_summary()
	context.stages = ["Material", "Fabrication", "Assembly", "Paint", "QC", "Delivery"]
	context.statuses = ["In Production", "In Stock", "Sold", "Delivered", "Scrapped"]
	context.page_count = len(context.rows)
	context.page_count_label = "serials"
	return context
