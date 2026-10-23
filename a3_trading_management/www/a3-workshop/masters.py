from a3_trading_management.website_utils import require_login

no_cache = 1


def get_context(context):
	require_login(context)
	context.title = "Items & Customers"
	context.page_icon = "fa-book"
	context.subtitle = "Items, customers and warehouses to look up, and projected stock (view only)"
	context.breadcrumb = "Items & Customers"

	from a3_trading_management.api.supervisor_portal import (
		list_customers, list_items, list_warehouse_stock, projected_stock,
	)

	context.item_rows = list_items()
	context.customers = list_customers()
	context.warehouses = list_warehouse_stock()
	context.projected = projected_stock()
	context.page_count = len(context.item_rows)
	context.page_count_label = "items"
	return context
