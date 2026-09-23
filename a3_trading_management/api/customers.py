# Implements: the Customers page -- one card per Customer with the two figures a
# trader wants at a glance: orders placed and total billed.
import frappe
from frappe.utils import cint, flt, fmt_money


def _currency():
	company = frappe.defaults.get_user_default("Company") or frappe.db.get_default("company")
	return (frappe.get_cached_value("Company", company, "default_currency") if company else None) \
		or frappe.defaults.get_global_default("currency")


@frappe.whitelist()
def list_customer_cards(search=None, limit=0):
	"""Every Customer (or those matching `search` on name or mobile), with the
	count of confirmed sales orders and the total invoiced. `limit=0` means all:
	the page filters in the browser, so a cap would only hide customers."""
	frappe.has_permission("Customer", "read", throw=True)
	or_filters = None
	if search:
		like = f"%{search}%"
		or_filters = [["customer_name", "like", like], ["mobile_no", "like", like]]
	rows = frappe.get_list(
		"Customer", fields=["name", "customer_name", "mobile_no", "email_id", "tax_id"],
		or_filters=or_filters, order_by="customer_name asc",
		limit_page_length=cint(limit) or 0,
	)
	if not rows:
		return []
	names = [r.name for r in rows]
	orders = {r.customer: r.n for r in frappe.get_all(
		"Sales Order", filters={"customer": ["in", names], "docstatus": 1},
		fields=["customer", "count(name) as n"], group_by="customer")}
	billed = {r.customer: r.total for r in frappe.get_all(
		"Sales Invoice", filters={"customer": ["in", names], "docstatus": ["<", 2]},
		fields=["customer", "sum(grand_total) as total"], group_by="customer")}
	contacts = _contact_fallbacks(names)
	currency = _currency()
	cards = []
	for r in rows:
		label = (r.customer_name or r.name).strip() or r.name
		fallback = contacts.get(r.name) or {}
		cards.append({
			"name": r.name,
			"customer_name": label,
			"initial": label[:1].upper(),
			"mobile_no": r.mobile_no or fallback.get("mobile_no") or "",
			"email_id": r.email_id or fallback.get("email_id") or "",
			"tax_id": r.tax_id or "",
			"orders": cint(orders.get(r.name)),
			"spent": fmt_money(flt(billed.get(r.name)), currency=currency),
		})
	return cards


def _contact_fallbacks(customers):
	"""{customer: {mobile_no, email_id}} from linked Contacts, for Customers whose
	own fields are blank. Primary contact first."""
	links = frappe.get_all(
		"Dynamic Link",
		filters={"parenttype": "Contact", "link_doctype": "Customer", "link_name": ["in", customers]},
		fields=["parent", "link_name"],
	)
	if not links:
		return {}
	owners = {}
	for link in links:
		owners.setdefault(link.parent, []).append(link.link_name)
	out = {}
	for c in frappe.get_all(
		"Contact", filters={"name": ["in", list(owners)]},
		fields=["name", "mobile_no", "phone", "email_id"],
		order_by="is_primary_contact desc, modified desc",
	):
		for customer in owners.get(c.name, []):
			entry = out.setdefault(customer, {"mobile_no": "", "email_id": ""})
			entry["mobile_no"] = entry["mobile_no"] or c.mobile_no or c.phone or ""
			entry["email_id"] = entry["email_id"] or c.email_id or ""
	return out


@frappe.whitelist()
def save_customer(name=None, customer_name=None, mobile_no=None, email_id=None, tax_id=None):
	"""The Customers pop-up in edit mode: contact details and TRN on an existing
	customer. Creation stays in create_customer."""
	if not name:
		return create_customer(customer_name, mobile_no, email_id, tax_id)
	frappe.has_permission("Customer", "write", doc=name, throw=True)
	doc = frappe.get_doc("Customer", name)
	mobile = (mobile_no or "").strip() or None
	email = (email_id or "").strip() or None
	# A customer's phone and email are fetched from its primary Contact on every
	# save, so the Contact is the record to change; the Customer then follows.
	if doc.customer_primary_contact:
		_update_contact(doc.customer_primary_contact, mobile, email)
	if customer_name and customer_name.strip() and customer_name.strip() != doc.customer_name:
		doc.customer_name = customer_name.strip()
	doc.mobile_no = mobile
	doc.email_id = email
	doc.tax_id = (tax_id or "").strip() or None
	doc.save()
	return {"name": doc.name, "customer_name": doc.customer_name}


def _update_contact(name, mobile, email):
	contact = frappe.get_doc("Contact", name)
	phone = next((r for r in contact.phone_nos if r.is_primary_mobile_no), None)
	if mobile:
		if phone:
			phone.phone = mobile
		else:
			contact.append("phone_nos", {"phone": mobile, "is_primary_mobile_no": 1})
	elif phone:
		contact.remove(phone)
	mail = next((r for r in contact.email_ids if r.is_primary), None)
	if email:
		if mail:
			mail.email_id = email
		else:
			contact.append("email_ids", {"email_id": email, "is_primary": 1})
	elif mail:
		contact.remove(mail)
	contact.flags.ignore_permissions = True
	contact.save()


@frappe.whitelist()
def create_customer(customer_name, mobile_no=None, email_id=None, tax_id=None):
	"""The Add Customer pop-up: name, phone, email, TRN. Group and territory take
	the Selling Settings defaults or the root records, so the portal never asks a
	salesperson a question the standard form would answer for them. Setting the
	phone and email on a new Customer makes ERPNext create its primary Contact."""
	frappe.has_permission("Customer", "create", throw=True)
	customer_name = (customer_name or "").strip()
	if not customer_name:
		frappe.throw(frappe._("Customer name is required"))
	existing = frappe.db.get_value("Customer", {"customer_name": customer_name}, "name")
	if existing:
		frappe.throw(frappe._("A customer called {0} already exists ({1}).").format(customer_name, existing))

	doc = frappe.new_doc("Customer")
	doc.customer_name = customer_name
	doc.customer_type = "Company" if tax_id else "Individual"
	doc.customer_group = (frappe.db.get_single_value("Selling Settings", "customer_group")
	                      or frappe.db.get_value("Customer Group", {"is_group": 0}, "name")
	                      or frappe.db.get_value("Customer Group", {}, "name"))
	doc.territory = (frappe.db.get_single_value("Selling Settings", "territory")
	                 or frappe.db.get_value("Territory", {"is_group": 0}, "name")
	                 or frappe.db.get_value("Territory", {}, "name"))
	doc.mobile_no = (mobile_no or "").strip() or None
	doc.email_id = (email_id or "").strip() or None
	if tax_id:
		doc.tax_id = tax_id.strip()
	doc.insert()
	return {"name": doc.name, "customer_name": doc.customer_name}
