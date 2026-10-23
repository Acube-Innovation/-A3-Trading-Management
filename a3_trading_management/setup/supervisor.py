"""Supervisor role (Redlines Trading): runs the production floor without seeing prices.

What the Supervisor does, and the access that gives it:

1.  Work Order          create and submit
2.  BOM                 create; the manager submits (approves) it
3.  Quality             create checklists (templates) and submit inspections
4.  Overtime            enter and submit Employee Overtime (its own doctype)
5.  Stock               Stock Entry in every warehouse, no warehouse restriction
6.  Quotation           create a draft; Sales adds the rates and submits it
7.  Staff request       raise a Job Requisition; HR approves it
8.  Material Request    create and submit
9.  Job Card            update and submit production progress
10. Item, Customer, Warehouse   read only
11. Stock reports       Stock Projected Qty only: quantities, no values
12. Never Delete, Cancel or Amend: reversing a posted entry is a manager's job
13. The "Supervisor" workspace is the only one in the sidebar (module profile)

Prices are hidden with a permission level, not with client scripts: every price
field (Currency fields plus a few percentages and amount-in-words) on the doctypes
above is moved to PRICE_LEVEL, and every role that can read the doctype is given
read (and write, where it had write) at that level. The Supervisor alone is not, so
forms, lists, report view, print and the timeline all leave the fields out. A child
field's level is checked against its parent's permissions, so a child table shared
with other documents (Sales Taxes and Charges on Sales Invoice, Purchase Order...)
is hidden whole on the Supervisor's doctypes instead of field by field.

Adding a rule to a doctype copies its standard permissions into Custom DocPerm,
exactly as the Role Permission Manager does, so later changes to the standard
permissions of those doctypes are not picked up on their own.

Idempotent: run on install and on every migrate, which also covers a role or a
doctype added later by another app.
"""

import frappe
from frappe.custom.doctype.property_setter.property_setter import make_property_setter
from frappe.permissions import setup_custom_perms

ROLE = "Supervisor"
ROLE_PROFILE = "Supervisor"
MODULE_PROFILE = "Supervisor"
WORKSPACE_MODULE = "A3 Trading Management"
PRICE_LEVEL = 5

# Every grant also carries read, report and print. Nothing here ever has delete,
# cancel or amend.
ACCESS = {
	"Work Order": ("create", "write", "submit"),
	"Job Card": ("create", "write", "submit"),
	"BOM": ("create", "write"),
	"Quality Inspection": ("create", "write", "submit"),
	"Quality Inspection Template": ("create", "write"),
	"Quality Inspection Parameter": ("create", "write"),
	"Stock Entry": ("create", "write", "submit"),
	"Material Request": ("create", "write", "submit"),
	"Serial and Batch Bundle": ("create", "write", "submit"),  # serial / batch picks on a stock entry
	"Quotation": ("create", "write"),
	"Job Requisition": ("create", "write"),
	"Item": (),
	"Customer": (),
	"Warehouse": (),
	"Serial No": (),
	"Batch": (),
	"Designation": (),
	"Department": (),
	"Stock Settings": (),  # the Stock Entry form reads it in the browser
	# Portal: the serial register, chassis plates (marked embossed on the floor) and
	# the stock quantities on Live Stock.
	"Trailer Serial": ("write",),
	"Bin": (),
}
# Who approves what the Supervisor raises, where the standard permissions leave
# them out: Frappe HR gives Job Requisition to System Manager only.
APPROVERS = {
	"Job Requisition": {"HR Manager": ("create", "write", "delete", "email"), "HR User": ("create", "write", "email")},
}
# Read limited to records the Supervisor created (none, in practice): forms that
# look these up on load (the Job Requisition form counts open Employee Referrals)
# get an empty answer instead of a permission error.
OWN_ONLY = ("Employee Referral",)
# Employee holds salary, bank and personal details: the Supervisor may pick one in a
# link field and nothing more. Employee Overtime fills the name in on the server.
SELECT_ONLY = ("Employee",)
BASE_RIGHTS = ("read", "report", "print")
ALL_RIGHTS = ("select", "read", "write", "create", "delete", "submit", "cancel", "amend",
              "report", "export", "import", "print", "email", "share")

# Prices that are not Currency fields; stock values are plain Floats.
PRICE_FIELDNAMES = {
	"discount_percentage", "additional_discount_percentage", "margin_rate_or_amount",
	"in_words", "base_in_words", "last_purchase_rate", "purchase_rate",
	"incoming_rate", "outgoing_rate", "stock_value_difference", "avg_rate", "total_amount",
	"valuation_rate", "stock_value",
}
# Shared child tables the Supervisor fills in, so they cannot be hidden whole: their
# price fields are hidden one by one and every parent (Routing, Stock Reservation
# Entry) gets the level as well.
FILLED_SHARED_TABLES = {"BOM Operation", "Serial and Batch Entry"}
# Core doctypes a link may point at that are not worth a permission rule of their own.
NO_SELECT = {"DocType", "User"}
# Required fields the Supervisor cannot fill: the pay is hidden from them, and
# Requested By would fetch from an Employee they cannot read. Made optional here;
# integrations.supervisor fills or asks for them at the step that needs them.
UNREQUIRE = {("Job Requisition", "expected_compensation"), ("Job Requisition", "requested_by")}

# Stock reports the Supervisor may run: quantities only.
REPORTS = ("Stock Projected Qty",)

AUTOMATIC_ROLES = ("Administrator", "Guest", "All", "Desk User")


def setup():
	if not frappe.db.exists("DocType", "Job Requisition"):
		return  # Frappe HR is not installed; staff requests and overtime need it
	ensure_role()
	p = plan()
	for doctype, fieldname in p["unrequire"]:
		make_property_setter(doctype, fieldname, "reqd", 0, "Check", validate_fields_for_doctype=False)
	for doctype, fieldnames in p["price_fields"].items():
		for fieldname in fieldnames:
			make_property_setter(doctype, fieldname, "permlevel", PRICE_LEVEL, "Int",
			                     validate_fields_for_doctype=False)
	for doctype, rights in ACCESS.items():
		grant(doctype, rights)
	for doctype in p["select_only"]:
		grant(doctype, (), select_only=True)
	for doctype in OWN_ONLY:
		grant(doctype, (), if_owner=1)
	for doctype, roles in APPROVERS.items():
		for role, rights in roles.items():
			if frappe.db.exists("Role", role):
				grant(doctype, rights, role=role, keep=True)
	# After the grants, so an approver added above sees the prices too.
	for doctype in p["price_level_doctypes"]:
		sync_price_level(doctype)
	for report in REPORTS:
		allow_report(report)
	setup_profiles()
	for doctype in {*p["price_level_doctypes"], *ACCESS, *p["select_only"]}:
		frappe.clear_cache(doctype=doctype)


def ensure_role():
	if not frappe.db.exists("Role", ROLE):
		frappe.get_doc({"doctype": "Role", "role_name": ROLE, "desk_access": 1}).insert(ignore_permissions=True)


def plan():
	"""Every change setup() makes, worked out without making any of them."""
	granted = [dt for dt in ACCESS if frappe.db.exists("DocType", dt)]
	price_fields, price_level_doctypes = {}, set()
	for doctype in granted:
		meta = frappe.get_meta(doctype)
		fields = _price_fields(meta)
		for tf in meta.get_table_fields():
			child_fields = _price_fields(frappe.get_meta(tf.options))
			if not child_fields:
				continue
			# A child field's level is checked against its parent's permissions. A
			# table only this doctype uses has its price fields hidden; a shared one
			# (Sales Taxes and Charges, Payment Schedule...) is hidden whole here, so
			# Sales Invoice, Purchase Order and the rest are left as they are.
			parents = _parents(tf.options)
			if parents == {doctype} or tf.options in FILLED_SHARED_TABLES:
				price_fields[tf.options] = child_fields
				price_level_doctypes |= parents
			elif not tf.permlevel:
				fields.append(tf.fieldname)
		if fields:
			price_fields[doctype] = fields
			price_level_doctypes.add(doctype)
	price_level_doctypes = sorted(price_level_doctypes)
	select_only = sorted({*SELECT_ONLY, *_editable_link_targets(granted)} - set(ACCESS) - NO_SELECT)
	return {
		"price_fields": price_fields,
		"price_level_doctypes": price_level_doctypes,
		"select_only": [dt for dt in select_only if frappe.db.exists("DocType", dt)],
		"unrequire": [(dt, f) for dt, f in UNREQUIRE if frappe.db.exists("DocType", dt)],
		"required_hidden": [(dt, f) for dt, fields in price_fields.items() for f in fields
		                    if frappe.get_meta(dt).get_field(f).reqd and (dt, f) not in UNREQUIRE],
	}


def _price_fields(meta):
	# A field at another level above 0 has a level of its own; the Supervisor holds
	# no level but 0, so it is hidden already. One at PRICE_LEVEL was moved by an
	# earlier run and is kept in the plan, so a later run still syncs its roles.
	return [
		df.fieldname for df in meta.fields
		if (df.permlevel or 0) in (0, PRICE_LEVEL)
		and (df.fieldtype == "Currency" or df.fieldname in PRICE_FIELDNAMES)
	]


def _parents(child):
	table = {"fieldtype": ["in", ["Table", "Table MultiSelect"]], "options": child}
	return {*frappe.get_all("DocField", filters=table, pluck="parent"),
	        *frappe.get_all("Custom Field", filters=table, pluck="dt")}


def _editable_link_targets(doctypes):
	"""Doctypes the Supervisor has to search in a link field of a form they edit."""
	targets = set()
	for doctype in doctypes:
		if not ACCESS[doctype]:
			continue  # read only: no link to fill in
		meta = frappe.get_meta(doctype)
		for m in [meta, *[frappe.get_meta(df.options) for df in meta.get_table_fields()]]:
			for df in m.fields:
				if df.fieldtype == "Link" and not df.read_only and not df.hidden and not df.permlevel:
					target = frappe.get_meta(df.options)
					if not target.istable and not target.issingle:
						targets.add(df.options)
	return targets


def sync_price_level(doctype):
	"""Give PRICE_LEVEL to every role that reads the doctype at level 0, the Supervisor
	excepted; write follows the role's level-0 write."""
	setup_custom_perms(doctype)
	perms = frappe.get_all("Custom DocPerm", filters={"parent": doctype},
	                       fields=["name", "role", "permlevel", "read", "write", "if_owner"])
	readers = {}
	for p in perms:
		if p.permlevel == 0 and p.read and p.role != ROLE:
			readers[p.role] = readers.get(p.role, 0) or p.write
	at_level = {p.role: p for p in perms if p.permlevel == PRICE_LEVEL and not p.if_owner}
	for role, write in readers.items():
		existing = at_level.get(role)
		if not existing:
			frappe.get_doc({
				"doctype": "Custom DocPerm", "parent": doctype, "parenttype": "DocType",
				"parentfield": "permissions", "role": role, "permlevel": PRICE_LEVEL, "read": 1, "write": write,
			}).insert(ignore_permissions=True)
		elif not existing.read or existing.write != write:
			frappe.db.set_value("Custom DocPerm", existing.name, {"read": 1, "write": write})
	for p in perms:
		if p.role == ROLE and p.permlevel:
			frappe.delete_doc("Custom DocPerm", p.name, ignore_permissions=True, force=True)


def grant(doctype, rights, select_only=False, role=ROLE, keep=False, if_owner=0):
	"""A role's level-0 rule on a doctype, set to exactly these rights. With keep, an
	existing rule is left as it is (an approver's rights may have been changed by hand).
	With if_owner, the rule covers only documents the user created."""
	if not frappe.db.exists("DocType", doctype):
		return
	setup_custom_perms(doctype)
	allowed = {"select"} if select_only else {"select", *BASE_RIGHTS, *rights}
	values = {r: int(r in allowed) for r in ALL_RIGHTS}
	name = frappe.db.get_value("Custom DocPerm", {"parent": doctype, "role": role, "permlevel": 0, "if_owner": if_owner})
	if not name:
		frappe.get_doc({
			"doctype": "Custom DocPerm", "parent": doctype, "parenttype": "DocType",
			"parentfield": "permissions", "role": role, "permlevel": 0, "if_owner": if_owner, **values,
		}).insert(ignore_permissions=True)
	elif keep or (select_only and frappe.db.get_value("Custom DocPerm", name, "read")):
		return  # a read grant from ACCESS (or by hand) is never cut back to select
	else:
		frappe.db.set_value("Custom DocPerm", name, values)


def allow_report(report):
	"""Custom roles replace a report's own roles, so they are copied over with the
	Supervisor added."""
	if not frappe.db.exists("Report", report):
		return
	custom = frappe.db.get_value("Custom Role", {"report": report})
	doc = frappe.get_doc("Custom Role", custom) if custom else frappe.new_doc("Custom Role")
	roles = [r.role for r in doc.get("roles")] if custom else frappe.get_all(
		"Has Role", filters={"parent": report, "parenttype": "Report"}, pluck="role")
	if ROLE in roles:
		return
	doc.report = report
	doc.ref_doctype = frappe.db.get_value("Report", report, "ref_doctype")
	doc.set("roles", [{"role": r} for r in [*roles, ROLE]])
	doc.save(ignore_permissions=True)


def setup_profiles():
	"""Role profile with the Supervisor role alone, and a module profile that leaves
	only the module holding the Supervisor workspace in the sidebar. Blocking a
	module only hides its workspaces; it does not change what a user may open."""
	if not frappe.db.exists("Role Profile", ROLE_PROFILE):
		profile = frappe.new_doc("Role Profile")
		profile.role_profile = ROLE_PROFILE
		profile.append("roles", {"role": ROLE})
		profile.save(ignore_permissions=True)

	modules = frappe.get_all("Module Def", filters={"name": ["!=", WORKSPACE_MODULE]}, pluck="name", order_by="name")
	if frappe.db.exists("Module Profile", MODULE_PROFILE):
		mp = frappe.get_doc("Module Profile", MODULE_PROFILE)
		if {m.module for m in mp.block_modules} == set(modules):
			return
	else:
		mp = frappe.new_doc("Module Profile")
		mp.module_profile_name = MODULE_PROFILE
	mp.set("block_modules", [{"module": m} for m in modules])
	mp.save(ignore_permissions=True)  # also updates users who hold the profile


def is_supervisor_only(user=None):
	"""True for a user whose only real role is Supervisor (Employee aside): such a
	user sees no prices anywhere, including the workshop portal, which is built on
	full access and is closed to them."""
	user = user or frappe.session.user
	if user in ("Administrator", "Guest"):
		return False
	roles = set(frappe.get_roles(user))
	return ROLE in roles and not roles - {ROLE, "Employee", *AUTOMATIC_ROLES}
