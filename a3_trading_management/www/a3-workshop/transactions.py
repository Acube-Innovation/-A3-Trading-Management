import frappe
from a3_trading_management.website_utils import require_login

no_cache = 1

# The buying, selling and accounts screens, gathered behind one menu entry. Each
# tile just links to the page that already exists — nothing is duplicated here.
GROUPS = [
	{
		"title": "Buying",
		"subtitle": "From a request for material through to paying the supplier",
		# The steps run in this order, so they are numbered on the page.
		"flow": True,
		# Suppliers is its own menu entry; it is repeated here because a purchase
		# order stops at a supplier whose papers have lapsed.
		"footer": {"label": "Suppliers", "route": "/a3-workshop/suppliers",
		           "icon": "fa-truck-field", "sub": "Compliance papers behind every order"},
		"tiles": [
			{"label": "Requisitions", "route": "/a3-workshop/purchase-requisitions",
			 "icon": "fa-clipboard-check", "sub": "Requests for material"},
			{"label": "Purchase Orders", "route": "/a3-workshop/purchase-orders",
			 "icon": "fa-file-contract", "sub": "Orders to suppliers"},
			{"label": "Goods Receipt", "route": "/a3-workshop/goods-receipt",
			 "icon": "fa-dolly", "sub": "Receive stock against an order"},
			{"label": "Purchase Invoices", "route": "/a3-workshop/purchase-invoices",
			 "icon": "fa-file-invoice", "sub": "Bills, held until approved"},
			{"label": "Supplier Payments", "route": "/a3-workshop/supplier-payments",
			 "icon": "fa-money-bill-transfer", "sub": "Pay and see what is outstanding"},
		],
	},
	{
		"title": "Selling",
		"subtitle": "Customer orders, dispatch against the serial, and the invoice",
		"flow": True,
		"tiles": [
			{"label": "Sales Orders", "route": "/a3-workshop/sales-orders",
			 "icon": "fa-cart-shopping", "sub": "Orders for trailers and parts"},
			{"label": "Awaiting Dispatch", "route": "/a3-workshop/awaiting-dispatch",
			 "icon": "fa-hourglass-half", "sub": "Confirmed orders waiting to leave the yard"},
			{"label": "Delivery Notes", "route": "/a3-workshop/delivery-notes",
			 "icon": "fa-truck-fast", "sub": "Trailers dispatched against their serial"},
			{"label": "Sales Invoices", "route": "/a3-workshop/sales-invoices",
			 "icon": "fa-file-invoice", "sub": "Bill the delivery and receive payment"},
		],
	},
	{
		"title": "Accounts",
		"subtitle": "The standard ledger — no separate books are kept",
		"tiles": [
			{"label": "Payments & Receipts", "route": "/a3-workshop/payments-receipts",
			 "icon": "fa-right-left", "sub": "Money in and money out"},
			{"label": "Journal Entries", "route": "/a3-workshop/journal-entries",
			 "icon": "fa-book", "sub": "Manual journals and adjustments"},
		],
	},
]


def _tabs():
	"""GROUPS as the page's tabs: one per group, one sub-tab per screen. The
	group's footer screen (Suppliers) is the last sub-tab of its group."""
	out = []
	for g in GROUPS:
		pages = [dict(t) for t in g["tiles"]] + ([dict(g["footer"])] if g.get("footer") else [])
		for t in pages:
			t["key"] = t["route"].rstrip("/").rsplit("/", 1)[-1]
		out.append({"key": g["title"].lower(), "title": g["title"], "subtitle": g["subtitle"], "pages": pages})
	return out


def get_context(context):
	require_login(context)
	context.title = "Operations"
	context.page_icon = "fa-file-invoice-dollar"
	context.subtitle = "Buying, selling and accounts"
	context.breadcrumb = "Operations"

	context.groups = GROUPS
	context.tabs = _tabs()
	context.page_count = sum(len(g["tiles"]) for g in GROUPS)
	context.page_count_label = "screens"
	return context
