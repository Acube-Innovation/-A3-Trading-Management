import frappe
from a3_trading_management.website_utils import require_login

no_cache = 1


def get_context(context):
	require_login(context)
	context.title = "Goods Receipt"
	context.page_icon = "fa-dolly"
	context.subtitle = "Receive stock against an order, with serial capture"
	context.breadcrumb = "Goods Receipt"

	from a3_trading_management.api.buying import list_goods_receipts, list_purchase_orders

	# ?material_request= or ?purchase_order= narrows both lists to one chain.
	context.material_request = (frappe.form_dict.get("material_request") or "").strip()
	context.purchase_order = (frappe.form_dict.get("purchase_order") or "").strip()
	context.receipts = list_goods_receipts(search=(frappe.form_dict.get("q") or "").strip() or None,
	                                       material_request=context.material_request or None,
	                                       purchase_order=context.purchase_order or None)
	# Orders still awaiting stock — what the receiving bay is working through.
	context.awaiting = [o for o in list_purchase_orders(status=None, material_request=context.material_request or None)
	                    if o.docstatus == 1 and (o.per_received or 0) < 100
	                    and (not context.purchase_order or o.name == context.purchase_order)]
	context.page_count = len(context.receipts)
	context.page_count_label = "receipts"
	return context
