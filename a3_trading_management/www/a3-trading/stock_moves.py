import frappe

from a3_trading_management.website_utils import require_login

no_cache = 1


def get_context(context):
	require_login(context)
	context.title = "Stock Moves"
	context.page_icon = "fa-right-left"
	context.subtitle = "Move stock between warehouses, issue it to the floor, or repack it"
	context.breadcrumb = "Stock Moves"

	from a3_trading_management.api._common import list_warehouses
	from a3_trading_management.api.supervisor_portal import list_stock_moves

	context.moves = list_stock_moves()
	context.warehouses = list_warehouses()
	context.default_warehouse = frappe.db.get_single_value("Stock Settings", "default_warehouse")
	# Repack from BOM: approved BOMs; ?repack_bom=<name> (the BOM screen's Repack
	# button) opens the pop-up on that BOM.
	context.repack_boms = frappe.get_all("BOM", filters={"docstatus": 1, "is_active": 1},
	                                     fields=["name", "item", "item_name", "quantity"], order_by="item_name, name")
	context.repack_bom = frappe.form_dict.get("repack_bom") or ""
	context.drafts = len([m for m in context.moves if m.docstatus == 0])
	context.page_count = len(context.moves)
	context.page_count_label = "stock entries"
	return context
