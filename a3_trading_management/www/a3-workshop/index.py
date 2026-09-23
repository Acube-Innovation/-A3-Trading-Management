import frappe
from a3_trading_management.website_utils import require_login

no_cache = 1


def get_context(context):
	require_login(context)
	context.title = "Home"
	context.page_icon = "fa-gauge-high"

	from a3_trading_management.api.dashboard import get_dashboard

	data = get_dashboard()
	context.tiles = data["tiles"]
	context.stages = data["stages"]
	context.work_orders = data["work_orders"]
	context.deliveries = data["deliveries"]
	context.low_stock = data["low_stock"]
	return context
