"""Invoice Deletion Request — the approval trail for deleting a posted invoice.

Operator raises it -> Finance (Accounts Manager) approves -> Owner approves, and
the Owner's approval is what actually deletes the invoice (api.invoice_delete).
The workflow "Invoice Deletion Approval" decides who may move it; this controller
records who did, keeps the three people different, and runs the deletion inside
the same save so a failure leaves both the request and the invoice untouched.
"""

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import now_datetime

from a3_trading_management.api import invoice_delete


class InvoiceDeletionRequest(Document):
	def before_insert(self):
		invoice_delete._check_type(self.invoice_type)
		if not frappe.db.exists(self.invoice_type, self.invoice):
			frappe.throw(_("{0} {1} does not exist.").format(self.invoice_type, self.invoice))
		existing = invoice_delete.open_request(self.invoice_type, self.invoice)
		if existing:
			frappe.throw(_("{0} already has an open deletion request: {1} ({2}).").format(
				self.invoice, existing.name, existing.workflow_state))
		self.requested_by = frappe.session.user
		self.requested_on = now_datetime()
		self._snapshot()

	def validate(self):
		if not self.has_value_changed("workflow_state") or self.is_new():
			return
		state, user = self.workflow_state, frappe.session.user
		if state == "Pending Finance":
			self._refresh_impact(block=True)
		elif state == "Pending Owner":
			self._distinct(user, self.requested_by, _("the person who raised it"))
			self._refresh_impact(block=True)
			self.finance_approved_by, self.finance_approved_on = user, now_datetime()
		elif state == "Deleted":
			self._distinct(user, self.requested_by, _("the person who raised it"))
			self._distinct(user, self.finance_approved_by, _("the Finance approver"))
			self.owner_approved_by, self.owner_approved_on = user, now_datetime()
		elif state == "Rejected":
			self.rejected_by, self.rejected_on = user, now_datetime()

	def before_save(self):
		if self.workflow_state == "Deleted" and self.has_value_changed("workflow_state") and not self.deleted_on:
			log = invoice_delete.delete_invoice(self.invoice_type, self.invoice)
			self.deleted_on = now_datetime()
			self.deletion_log = "\n".join(log)

	def _distinct(self, user, other, who):
		if user != "Administrator" and other and user == other:
			frappe.throw(_("You cannot approve this request because you are {0}. "
			               "A different person must approve it.").format(who))

	def _snapshot(self):
		inv = frappe.get_doc(self.invoice_type, self.invoice)
		self.party = inv.get("customer_name") or inv.get("supplier_name") or inv.get("customer") or inv.get("supplier")
		self.company = inv.company
		self.posting_date = inv.posting_date
		self.grand_total = inv.grand_total
		self.outstanding_amount = inv.outstanding_amount
		self.currency = inv.currency
		self.invoice_status = inv.status
		self._refresh_impact(block=True)

	def _refresh_impact(self, block):
		if not frappe.db.exists(self.invoice_type, self.invoice):
			frappe.throw(_("{0} {1} no longer exists.").format(self.invoice_type, self.invoice))
		impact = invoice_delete.get_impact(self.invoice_type, self.invoice)
		self.impact = invoice_delete.impact_text(impact)
		if block and impact["blockers"]:
			frappe.throw("<br>".join(impact["blockers"]), title=_("{0} cannot be deleted yet").format(self.invoice))
