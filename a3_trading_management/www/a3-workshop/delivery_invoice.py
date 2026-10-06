import frappe

no_cache = 1


def get_context(context):
	"""The old combined Delivery & Invoice screen is now three screens (Awaiting
	Dispatch, Delivery Notes, Sales Invoices). Old links and bookmarks land on the
	first of them."""
	frappe.local.flags.redirect_location = "/a3-workshop/awaiting-dispatch"
	raise frappe.Redirect
