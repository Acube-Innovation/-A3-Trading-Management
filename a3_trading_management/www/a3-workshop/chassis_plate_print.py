import frappe
from a3_trading_management.website_utils import require_login

no_cache = 1


def get_context(context):
	require_login(context)
	context.title = "Chassis Plate Print"
	context.page_icon = "fa-print"
	context.subtitle = "Embossing sheet for the fabrication bay"
	context.breadcrumb = "Chassis Plate Print"

	from a3_trading_management.api.serial_register import get_plate_batch, list_serials

	# ?work_order= prints the whole batch; ?serial= prints one trailer. With
	# neither, the bay gets everything still awaiting embossing — which is the
	# sheet it works from most of the time.
	work_order = frappe.form_dict.get("work_order")
	serial = frappe.form_dict.get("serial")

	if serial:
		context.rows = get_plate_batch(serials=[serial])
	elif work_order:
		context.rows = get_plate_batch(work_order=work_order)
	else:
		context.rows = list_serials(embossed=0, limit=200)

	context.work_order = work_order
	context.single_serial = serial
	context.page_count = len(context.rows)
	context.page_count_label = "plates"
	return context
