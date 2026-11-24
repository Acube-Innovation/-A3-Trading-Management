"""Only a Super Admin submits the commercial documents.

Anyone may create and save a draft; submitting (Approve on the portal, Submit on
the Desk) is kept for users holding the "Super Admin" role (given by the Super
Admin role profile) and for Administrator. Hooked on before_submit for every
doctype ("*") and narrowed to SUPER_ADMIN_SUBMIT, so the portal, the Desk and
the API all follow the same rule.

Shop-floor documents (Work Order, Stock Entry, Quality Inspection, BOM,
overtime) are not in the list: production submits those as it moves."""

import frappe
from frappe import _

SUPER_ADMIN_ROLE = "Super Admin"

SUPER_ADMIN_SUBMIT = (
	"Quotation",
	"Sales Order",
	"Delivery Note",
	"Sales Invoice",
	"Material Request",
	"Purchase Order",
	"Purchase Receipt",
	"Purchase Invoice",
	"Payment Entry",
	"Journal Entry",
)


def can_submit(user=None):
	user = user or frappe.session.user
	return user == "Administrator" or SUPER_ADMIN_ROLE in frappe.get_roles(user)


def before_submit(doc, method=None):
	if doc.doctype not in SUPER_ADMIN_SUBMIT:
		return
	if frappe.flags.in_install or frappe.flags.in_migrate or frappe.flags.in_patch or frappe.flags.in_test:
		return
	if can_submit():
		return
	frappe.throw(
		_("Submission only for Super Admin. {0} {1} stays saved as a draft; ask a Super Admin to submit it.").format(
			_(doc.doctype), doc.name),
		frappe.PermissionError,
		title=_("Submission only for Super Admin"),
	)
