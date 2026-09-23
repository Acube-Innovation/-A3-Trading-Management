import frappe
from a3_trading_management.website_utils import require_login

no_cache = 1


def get_context(context):
	require_login(context)
	context.title = "Live Stock"
	context.page_icon = "fa-boxes-stacked"
	context.subtitle = "Stock monitoring board — warehouses, alerts, requisitions & payments"
	context.breadcrumb = "Live Stock"

	# One read-only call assembles the whole board (a3_trading_management.api.live_stock);
	from a3_trading_management.api.live_stock import get_stock_board

	context.board = get_stock_board()

	# The board's headline list is the stock table, so that is what the topbar counts.
	context.page_count = len(context.board.get("stock") or [])
	context.page_count_label = "stock rows"
	return context
