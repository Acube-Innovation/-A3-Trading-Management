"""Roles and workflow for Invoice Deletion Request (operator -> finance -> owner).

Operator raises the request; Finance (Accounts Manager or Finance Manager)
approves; the Owner, who holds the "Super Admin" role profile (every role),
gives the final approval that deletes the invoice.

Built in code, not as a fixture, because the workflow links to Workflow State and
Workflow Action Master records that have to exist first. Idempotent: run on
install and on every migrate, it brings the workflow back to this definition.
"""

import frappe

WORKFLOW = "Invoice Deletion Approval"
DOCTYPE = "Invoice Deletion Request"
OPERATOR, OWNER = "Operator", "Super Admin"
FINANCE = ("Accounts Manager", "Finance Manager")
NEW_ROLES = ("Operator", "Finance Manager", "Super Admin")
RETIRED_ROLES = ("A3 Operator", "A3 Owner")  # first version of this workflow
OWNER_PROFILE = "Super Admin"
# Assigned automatically by Frappe, never through a profile.
AUTOMATIC_ROLES = ("Administrator", "Guest", "All", "Desk User")

STATES = [
	# state, style, role that may edit the request while it sits here
	("Draft", "", OPERATOR),
	*[("Pending Finance", "Warning", role) for role in FINANCE],
	("Pending Owner", "Warning", OWNER),
	("Deleted", "Danger", "System Manager"),
	("Rejected", "Inverse", "System Manager"),
]
TRANSITIONS = [
	# from, action, to, role
	("Draft", "Submit for Approval", "Pending Finance", OPERATOR),
	*[t for role in FINANCE for t in (
		("Pending Finance", "Approve", "Pending Owner", role),
		("Pending Finance", "Reject", "Rejected", role),
	)],
	("Pending Owner", "Approve", "Deleted", OWNER),
	("Pending Owner", "Reject", "Rejected", OWNER),
]


def setup():
	if not frappe.db.exists("DocType", DOCTYPE):
		return
	for role in NEW_ROLES:
		if not frappe.db.exists("Role", role):
			frappe.get_doc({"doctype": "Role", "role_name": role, "desk_access": 1}).insert(ignore_permissions=True)
	for role in RETIRED_ROLES:
		if frappe.db.exists("Role", role) and not frappe.db.exists("Has Role", {"role": role, "parenttype": "User"}):
			frappe.delete_doc("Role", role, ignore_permissions=True, force=True)
	for state, style, _role in STATES:
		if not frappe.db.exists("Workflow State", state):
			frappe.get_doc({"doctype": "Workflow State", "workflow_state_name": state, "style": style}).insert(
				ignore_permissions=True)
	for _from, action, _to, _role in TRANSITIONS:
		if not frappe.db.exists("Workflow Action Master", action):
			frappe.get_doc({"doctype": "Workflow Action Master", "workflow_action_name": action}).insert(
				ignore_permissions=True)

	wf = frappe.get_doc("Workflow", WORKFLOW) if frappe.db.exists("Workflow", WORKFLOW) else frappe.new_doc("Workflow")
	wf.update({
		"workflow_name": WORKFLOW,
		"document_type": DOCTYPE,
		"workflow_state_field": "workflow_state",
		"is_active": 1,
		"override_status": 0,
		"send_email_alert": 0,
	})
	wf.set("states", [{"state": s, "doc_status": "0", "allow_edit": role} for s, _style, role in STATES])
	# Self-approval off: whoever raised the request cannot approve it. The
	# controller also keeps the Finance and Owner approvers different people.
	wf.set("transitions", [
		{"state": f, "action": a, "next_state": t, "allowed": role,
		 "allow_self_approval": 1 if a in ("Submit for Approval", "Reject") else 0}
		for f, a, t, role in TRANSITIONS
	])
	wf.flags.ignore_permissions = True
	wf.save()
	setup_owner_profile()


def setup_owner_profile():
	"""The Owner's role profile: every Desk role on the site. Re-synced on migrate so
	a role added later (a new app, a new module) reaches the Owner too. Portal-only
	roles (Customer, Supplier...) are left out: they are for website logins."""
	roles = frappe.get_all("Role", filters={"disabled": 0, "desk_access": 1, "name": ["not in", AUTOMATIC_ROLES]},
	                       pluck="name", order_by="name")
	if frappe.db.exists("Role Profile", OWNER_PROFILE):
		profile = frappe.get_doc("Role Profile", OWNER_PROFILE)
		if {r.role for r in profile.roles} == set(roles):
			return
	else:
		profile = frappe.new_doc("Role Profile")
		profile.role_profile = OWNER_PROFILE
	profile.set("roles", [{"role": r} for r in roles])
	profile.save(ignore_permissions=True)  # also updates users who hold the profile
