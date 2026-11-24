import frappe

from a3_trading_management.website_utils import require_login

no_cache = 1


def get_context(context):
	require_login(context)
	context.title = "Statement of Account"
	context.page_icon = "fa-file-invoice-dollar"
	context.subtitle = "A customer's or supplier's account: opening balance, every invoice, receipt and note, closing balance"
	context.breadcrumb = "Statement of Account"
	fd = frappe.form_dict
	context.party_type = fd.get("party_type") if fd.get("party_type") in ("Customer", "Supplier") else "Customer"
	context.party = fd.get("party") or ""
	context.party_label = ""
	if context.party and frappe.db.exists(context.party_type, context.party):
		field = "customer_name" if context.party_type == "Customer" else "supplier_name"
		context.party_label = frappe.db.get_value(context.party_type, context.party, field) or context.party
	context.from_date = fd.get("from_date") or ""
	context.to_date = fd.get("to_date") or ""
	return context
