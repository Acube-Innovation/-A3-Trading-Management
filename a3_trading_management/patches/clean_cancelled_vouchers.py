"""24 Nov 2026 (user): cancelled Journal Entries, Payment Entries, Purchase and
Sales Invoices, Stock Entries and Quotations are deleted, and the amendment that survives a chain takes the original
number back: ACC-PAY-2026-00014 (cancelled) -> -1 (cancelled) -> -2 (submitted)
leaves ACC-PAY-2026-00014, the old -2.

The ledger rows a cancelled voucher left behind (GL / Payment Ledger / Stock
Ledger, all is_cancelled) go with it; a renamed voucher's rows follow its new
name (rename_doc updates every link and dynamic link)."""

import re

import frappe

# 24 Nov 2026, later (user): Sales Invoice, Stock Entry and Quotation as well.
DOCTYPES = ("Journal Entry", "Payment Entry", "Purchase Invoice", "Sales Invoice", "Stock Entry", "Quotation")
# (ledger, column marking a reversed row)
LEDGERS = (("GL Entry", "is_cancelled"), ("Payment Ledger Entry", "delinked"), ("Advance Payment Ledger Entry", "delinked"),
           ("Stock Ledger Entry", "is_cancelled"))


def _deleted_original(dt, name):
	"""An amendment ("<original>-N") whose original was deleted before."""
	base = re.sub(r"-\d+$", "", name)
	return base != name and bool(frappe.db.exists("Deleted Document", {"deleted_doctype": dt, "deleted_name": base}))


# Free text that names a voucher ("... against Sales Invoice ACC-SINV-2026-00004-1").
REMARKS = (("GL Entry", "remarks"), ("Payment Ledger Entry", "remarks"), ("Payment Entry", "remarks"),
           ("Journal Entry", "user_remark"), ("Journal Entry", "remark"), ("Purchase Invoice", "remarks"), ("Sales Invoice", "remarks"))


def _rename_in_remarks(old, new):
	pat = re.escape(old) + "([^0-9]|$)"
	for table, col in REMARKS:
		if frappe.db.has_column(table, col):
			frappe.db.sql("update `tab%s` set `%s` = regexp_replace(`%s`, %%s, %%s) where `%s` regexp %%s" % (table, col, col, col),
			              (pat, new + "\\1", pat))


def plan():
	"""[(doctype, [names to delete], [(old, new) renames])]"""
	out = []
	for dt in DOCTYPES:
		cancelled = frappe.get_all(dt, filters={"docstatus": 2}, pluck="name", order_by="name")
		renames = []
		live = frappe.get_all(dt, filters={"docstatus": ["<", 2]}, fields=["name", "amended_from"])
		for name in [r.name for r in live if r.amended_from or _deleted_original(dt, r.name)]:
			# An amendment adds one "-N" to the original name (a re-amendment counts on: -1, -2...).
			# Also an amendment whose original was deleted earlier (amended_from is then
			# empty): its original name is on record in Deleted Document.
			base = re.sub(r"-\d+$", "", name)
			# Only when every earlier version of the chain is cancelled (so deleted here).
			chain = frappe.get_all(dt, filters={"name": ["like", base + "%"]}, fields=["name", "docstatus"])
			others = [c for c in chain if c.name != name and (c.name == base or c.name.startswith(base + "-"))]
			if all(c.docstatus == 2 for c in others):
				renames.append((name, base))
		out.append((dt, cancelled, renames))
	return out


def execute(dry_run=False):
	"""Each delete / rename on its own: one that fails is rolled back, logged and
	skipped, so a single odd document never stops the whole migrate."""
	from frappe.model.rename_doc import rename_doc

	for dt, cancelled, renames in plan():
		for name in cancelled:
			if dry_run:
				print("delete", dt, name)
				continue
			frappe.db.savepoint("clean_voucher")
			try:
				# Unlink a later amendment so the delete is not refused for it.
				frappe.db.sql("update `tab%s` set amended_from = null where amended_from = %%s" % dt, name)
				frappe.delete_doc(dt, name, force=1, ignore_permissions=True, delete_permanently=True)
				for ledger, flag in LEDGERS:
					frappe.db.sql("delete from `tab%s` where voucher_type = %%s and voucher_no = %%s and %s = 1" % (ledger, flag), (dt, name))
			except Exception:
				frappe.db.rollback(save_point="clean_voucher")
				frappe.log_error(title="clean_cancelled_vouchers: not deleted %s %s" % (dt, name))
		for old, new in renames:
			if dry_run:
				print("rename", dt, old, "->", new)
				continue
			if frappe.db.exists(dt, new):  # its cancelled original could not be deleted
				continue
			frappe.db.savepoint("clean_voucher")
			try:
				rename_doc(dt, old, new, force=True, ignore_permissions=True, show_alert=False, rebuild_search=False)
				_rename_in_remarks(old, new)
			except Exception:
				frappe.db.rollback(save_point="clean_voucher")
				frappe.log_error(title="clean_cancelled_vouchers: not renamed %s %s" % (dt, old))
