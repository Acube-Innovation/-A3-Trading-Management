"""Accounts on the portal (24 Nov 2026): Statement of Account, Accounts Receivable
(and Payable), the General Ledger, the bank / receipt vouchers and the credit /
debit notes.

Everything reads the real ledger (GL Entry) or the real documents; nothing here
keeps its own books. New vouchers and notes are saved as drafts: only a Super
Admin submits (api/submit_guard), the same as every other document.

The statement, receivable and ledger prints are bilingual (English / Arabic) and
show the currency as "AED" (the Currency record's symbol is the Arabic sign).
"""

import base64
import os

import frappe
from frappe import _
from frappe.utils import add_days, cint, date_diff, flt, formatdate, getdate, get_first_day, nowdate, now_datetime

from a3_trading_management.api._common import default_company, set_payment_reference

PARTY_TYPES = ("Customer", "Supplier")


def _company(company=None):
	return company or default_company()


def _dates(from_date=None, to_date=None):
	to_date = getdate(to_date or nowdate())
	from_date = getdate(from_date) if from_date else get_first_day(add_days(to_date, -365))
	if from_date > to_date:
		frappe.throw(_("From date is after To date"))
	return from_date, to_date


def _check_party(party_type, party):
	if party_type not in PARTY_TYPES:
		frappe.throw(_("Choose Customer or Supplier"))
	if not party or not frappe.db.exists(party_type, party):
		frappe.throw(_("Choose a {0}").format(_(party_type).lower()))


# ---------------------------------------------------------------------------
# Voucher labels: what the reader calls each line
# ---------------------------------------------------------------------------

def _labels(entries):
	"""{(voucher_type, voucher_no): (label, description)} for statement / ledger lines."""
	by_type = {}
	for e in entries:
		by_type.setdefault(e.voucher_type, set()).add(e.voucher_no)
	out = {}
	if by_type.get("Sales Invoice"):
		for r in frappe.get_all("Sales Invoice", filters={"name": ["in", list(by_type["Sales Invoice"])]},
		                        fields=["name", "is_return", "return_against", "po_no", "remarks"]):
			desc = ("Against " + r.return_against) if r.is_return and r.return_against else (("PO " + r.po_no) if r.po_no else "")
			out[("Sales Invoice", r.name)] = ("Credit Note" if r.is_return else "Tax Invoice", desc)
	if by_type.get("Purchase Invoice"):
		for r in frappe.get_all("Purchase Invoice", filters={"name": ["in", list(by_type["Purchase Invoice"])]},
		                        fields=["name", "is_return", "return_against", "bill_no"]):
			desc = ("Against " + r.return_against) if r.is_return and r.return_against else (("Bill " + r.bill_no) if r.bill_no else "")
			out[("Purchase Invoice", r.name)] = ("Debit Note" if r.is_return else "Purchase Invoice", desc)
	if by_type.get("Payment Entry"):
		for r in frappe.get_all("Payment Entry", filters={"name": ["in", list(by_type["Payment Entry"])]},
		                        fields=["name", "payment_type", "mode_of_payment", "reference_no", "paid_from", "paid_to"]):
			out[("Payment Entry", r.name)] = (_pe_label(r), " · ".join(x for x in [r.mode_of_payment, ("Ref " + r.reference_no) if r.reference_no else ""] if x))
	if by_type.get("Journal Entry"):
		for r in frappe.get_all("Journal Entry", filters={"name": ["in", list(by_type["Journal Entry"])]},
		                        fields=["name", "voucher_type", "user_remark", "cheque_no"]):
			label = {"Journal Entry": "Journal", "Bank Entry": "Bank Voucher", "Cash Entry": "Cash Voucher"}.get(r.voucher_type, r.voucher_type)
			out[("Journal Entry", r.name)] = (label, (r.user_remark or "")[:80])
	return out


def _pe_label(pe):
	"""Receipt / payment voucher name by the account the money went through."""
	if pe.payment_type == "Internal Transfer":
		return "Transfer"
	acc = pe.paid_to if pe.payment_type == "Receive" else pe.paid_from
	kind = frappe.db.get_value("Account", acc, "account_type") if acc else None
	if pe.payment_type == "Receive":
		return "Bank Receipt" if kind == "Bank" else "Cash Receipt" if kind == "Cash" else "Receipt"
	return "Bank Payment" if kind == "Bank" else "Cash Payment" if kind == "Cash" else "Payment"


# ---------------------------------------------------------------------------
# Statement of Account
# ---------------------------------------------------------------------------

@frappe.whitelist()
def statement_of_account(party_type, party, from_date=None, to_date=None, company=None):
	"""A customer's or supplier's account for a period: opening balance, every
	invoice / receipt / note / journal in date order with a running balance, the
	closing balance, and the ageing of what is still open."""
	frappe.has_permission("GL Entry", "read", throw=True)
	_check_party(party_type, party)
	company = _company(company)
	from_date, to_date = _dates(from_date, to_date)
	base = {"company": company, "party_type": party_type, "party": party}

	opening = frappe.db.sql(
		"""select coalesce(sum(debit - credit), 0) from `tabGL Entry`
		where company=%(company)s and party_type=%(party_type)s and party=%(party)s
		  and is_cancelled=0 and posting_date < %(from_date)s""",
		dict(base, from_date=from_date))[0][0]
	entries = frappe.db.sql(
		"""select posting_date, voucher_type, voucher_no, sum(debit) as debit, sum(credit) as credit,
		          min(creation) as created
		from `tabGL Entry`
		where company=%(company)s and party_type=%(party_type)s and party=%(party)s
		  and is_cancelled=0 and posting_date between %(from_date)s and %(to_date)s
		group by posting_date, voucher_type, voucher_no
		order by posting_date, created""",
		dict(base, from_date=from_date, to_date=to_date), as_dict=True)
	labels = _labels(entries)
	balance = flt(opening)
	lines = []
	for e in entries:
		if not flt(e.debit) and not flt(e.credit):
			continue
		balance += flt(e.debit) - flt(e.credit)
		label, desc = labels.get((e.voucher_type, e.voucher_no), (e.voucher_type, ""))
		lines.append({"date": e.posting_date, "voucher_type": e.voucher_type, "voucher_no": e.voucher_no,
		              "label": label, "description": desc, "debit": flt(e.debit, 2), "credit": flt(e.credit, 2),
		              "balance": flt(balance, 2)})

	name_field = "customer_name" if party_type == "Customer" else "supplier_name"
	info = frappe.db.get_value(party_type, party, [name_field, "tax_id", "mobile_no", "email_id"], as_dict=True) or {}
	open_items, ageing = _open_items(party_type, party, company, to_date)
	return {
		"company": company, "party_type": party_type, "party": party,
		"party_name": info.get(name_field) or party, "tax_id": info.get("tax_id"),
		"mobile_no": info.get("mobile_no"), "email_id": info.get("email_id"),
		"address": _address(party_type, party),
		"from_date": str(from_date), "to_date": str(to_date),
		"opening": flt(opening, 2), "lines": lines, "closing": flt(balance, 2),
		"total_debit": flt(sum(l["debit"] for l in lines), 2),
		"total_credit": flt(sum(l["credit"] for l in lines), 2),
		"open_items": open_items, "ageing": ageing, "currency": _currency(company),
	}


def _address(party_type, party):
	links = frappe.get_all("Dynamic Link", filters={"link_doctype": party_type, "link_name": party, "parenttype": "Address"},
	                       pluck="parent", limit=1)
	if not links:
		return ""
	a = frappe.db.get_value("Address", links[0], ["address_line1", "address_line2", "city", "country"], as_dict=True) or {}
	return ", ".join(x for x in [a.get("address_line1"), a.get("address_line2"), a.get("city"), a.get("country")] if x)


def _currency(company):
	return frappe.db.get_value("Company", company, "default_currency") or "AED"


BUCKETS = ("Not due", "1-30", "31-60", "61-90", "90+")


def _bucket(age):
	age = cint(age)
	if age <= 0:
		return "Not due"
	if age <= 30:
		return "1-30"
	if age <= 60:
		return "31-60"
	if age <= 90:
		return "61-90"
	return "90+"


def _ar_rows(party_type, company, report_date, party=None):
	"""ERPNext's own Accounts Receivable / Payable lines (per open voucher, net of
	payments, credit notes and advances), aged on the due date."""
	from erpnext.accounts.report.accounts_receivable.accounts_receivable import ReceivablePayableReport

	filters = {"company": company, "report_date": report_date, "ageing_based_on": "Due Date",
	           "range": "30, 60, 90", "party_type": party_type}
	if party:
		filters["party"] = [party]
	args = {"account_type": "Receivable" if party_type == "Customer" else "Payable",
	        "naming_by": ["Selling Settings", "cust_master_name"] if party_type == "Customer" else ["Buying Settings", "supp_master_name"]}
	data = ReceivablePayableReport(filters).run(args)[1] or []
	return [r for r in data if isinstance(r, dict) and r.get("voucher_no") and abs(flt(r.get("outstanding"))) >= 0.005]


def _open_items(party_type, party, company, report_date):
	items, ageing = [], {b: 0.0 for b in BUCKETS}
	for r in _ar_rows(party_type, company, report_date, party):
		b = _bucket(r.get("age"))
		ageing[b] += flt(r.get("outstanding"))
		items.append({"voucher_type": r.get("voucher_type"), "voucher_no": r.get("voucher_no"),
		              "posting_date": r.get("posting_date"), "due_date": r.get("due_date"),
		              "invoiced": flt(r.get("invoiced"), 2), "paid": flt(r.get("paid"), 2),
		              "credit_note": flt(r.get("credit_note"), 2), "outstanding": flt(r.get("outstanding"), 2),
		              "age": cint(r.get("age")), "bucket": b})
	items.sort(key=lambda i: (str(i["due_date"] or ""), i["voucher_no"]))
	ageing["Total"] = sum(ageing[b] for b in BUCKETS)
	return items, {k: flt(v, 2) for k, v in ageing.items()}


# ---------------------------------------------------------------------------
# Accounts Receivable / Payable summary
# ---------------------------------------------------------------------------

@frappe.whitelist()
def receivable_summary(party_type="Customer", report_date=None, company=None):
	"""Per customer (or supplier): what is outstanding, split by age."""
	frappe.has_permission("GL Entry", "read", throw=True)
	if party_type not in PARTY_TYPES:
		frappe.throw(_("Choose Customer or Supplier"))
	company = _company(company)
	report_date = getdate(report_date or nowdate())
	out = {}
	for r in _ar_rows(party_type, company, report_date):
		p = out.setdefault(r.get("party"), {"party": r.get("party"), "party_name": r.get("party_name") or r.get("party"),
		                                    "invoiced": 0.0, "paid": 0.0, "credit_note": 0.0, "outstanding": 0.0,
		                                    "count": 0, **{b: 0.0 for b in BUCKETS}})
		p["invoiced"] += flt(r.get("invoiced"))
		p["paid"] += flt(r.get("paid"))
		p["credit_note"] += flt(r.get("credit_note"))
		p["outstanding"] += flt(r.get("outstanding"))
		p[_bucket(r.get("age"))] += flt(r.get("outstanding"))
		p["count"] += 1
	parties = sorted(out.values(), key=lambda p: -p["outstanding"])
	names = {}
	if parties:
		field = "customer_name" if party_type == "Customer" else "supplier_name"
		names = dict(frappe.get_all(party_type, filters={"name": ["in", [p["party"] for p in parties]]}, fields=["name", field], as_list=True))
	for p in parties:
		p["party_name"] = names.get(p["party"]) or p["party_name"]
		for k in ("invoiced", "paid", "credit_note", "outstanding", *BUCKETS):
			p[k] = flt(p[k], 2)
	totals = {k: flt(sum(p[k] for p in parties), 2) for k in ("invoiced", "paid", "credit_note", "outstanding", *BUCKETS)}
	return {"company": company, "party_type": party_type, "report_date": str(report_date),
	        "parties": parties, "totals": totals, "currency": _currency(company)}


# ---------------------------------------------------------------------------
# General Ledger, one account at a time
# ---------------------------------------------------------------------------

@frappe.whitelist()
def general_ledger(account, from_date=None, to_date=None, party_type=None, party=None, company=None):
	"""Every posting to one ledger account in a period, with a running balance."""
	frappe.has_permission("GL Entry", "read", throw=True)
	company = _company(company)
	acc = frappe.db.get_value("Account", account, ["name", "account_name", "root_type", "account_type", "is_group", "lft", "rgt", "company"], as_dict=True)
	if not acc or acc.company != company:
		frappe.throw(_("Choose an account"))
	from_date, to_date = _dates(from_date, to_date)
	accounts = frappe.get_all("Account", filters={"company": company, "lft": [">=", acc.lft], "rgt": ["<=", acc.rgt]}, pluck="name") if acc.is_group else [acc.name]
	cond, args = "", {"company": company, "accounts": accounts, "from_date": from_date, "to_date": to_date}
	if party_type and party:
		cond = " and party_type=%(party_type)s and party=%(party)s"
		args.update(party_type=party_type, party=party)
	opening = frappe.db.sql(
		f"""select coalesce(sum(debit - credit), 0) from `tabGL Entry`
		where company=%(company)s and account in %(accounts)s and is_cancelled=0 and posting_date < %(from_date)s{cond}""", args)[0][0]
	entries = frappe.db.sql(
		f"""select posting_date, account, voucher_type, voucher_no, party_type, party, against,
		          debit, credit, remarks, creation
		from `tabGL Entry`
		where company=%(company)s and account in %(accounts)s and is_cancelled=0
		  and posting_date between %(from_date)s and %(to_date)s{cond}
		order by posting_date, creation""", args, as_dict=True)
	labels = _labels(entries)
	balance = flt(opening)
	lines = []
	for e in entries:
		balance += flt(e.debit) - flt(e.credit)
		label, desc = labels.get((e.voucher_type, e.voucher_no), (e.voucher_type, ""))
		lines.append({"date": e.posting_date, "voucher_type": e.voucher_type, "voucher_no": e.voucher_no, "label": label,
		              "account": e.account, "party": e.party or "", "against": (e.against or "")[:60],
		              "description": desc or (e.remarks or "")[:80],
		              "debit": flt(e.debit, 2), "credit": flt(e.credit, 2), "balance": flt(balance, 2)})
	return {"company": company, "account": acc.name, "account_name": acc.account_name, "root_type": acc.root_type,
	        "party_type": party_type or "", "party": party or "",
	        "from_date": str(from_date), "to_date": str(to_date), "opening": flt(opening, 2), "lines": lines,
	        "closing": flt(balance, 2), "total_debit": flt(sum(l["debit"] for l in lines), 2),
	        "total_credit": flt(sum(l["credit"] for l in lines), 2), "currency": _currency(company)}


@frappe.whitelist()
def money_accounts(kind=None):
	"""Bank and cash ledgers a voucher can go through (kind: Bank / Cash / None for both)."""
	frappe.has_permission("Account", "read", throw=True)
	types = [kind] if kind in ("Bank", "Cash") else ["Bank", "Cash"]
	return frappe.get_all("Account", filters={"company": _company(), "is_group": 0, "disabled": 0, "account_type": ["in", types]},
	                      fields=["name", "account_name", "account_type"], order_by="account_type, account_name")


# ---------------------------------------------------------------------------
# Vouchers: Payment Receipt, Bank Receipt, Bank Payment
# ---------------------------------------------------------------------------

@frappe.whitelist()
def open_invoices(party_type, party):
	"""A party's posted invoices with something still to pay, oldest first."""
	_check_party(party_type, party)
	dt = "Sales Invoice" if party_type == "Customer" else "Purchase Invoice"
	frappe.has_permission(dt, "read", throw=True)
	return frappe.get_all(dt, filters={scrub_party(party_type): party, "docstatus": 1, "outstanding_amount": [">", 0]},
	                      fields=["name", "posting_date", "due_date", "grand_total", "outstanding_amount"],
	                      order_by="posting_date, name")


def scrub_party(party_type):
	return "customer" if party_type == "Customer" else "supplier"


@frappe.whitelist()
def create_voucher(direction, money_account, amount, party_type=None, party=None, account=None,
                   posting_date=None, mode_of_payment=None, reference_no=None, reference_date=None,
                   remark=None, invoices=None):
	"""Money in (direction=Receive) or out (Pay) through a bank or cash ledger.

	With a party (customer / supplier) it is a Payment Entry, allocated against the
	party's open invoices: the ones ticked (`invoices`, JSON list of names) or,
	if none are ticked, oldest first; whatever is left stays on account as an
	advance. Without a party it is a Journal Entry (Bank Entry / Cash Entry)
	against the account given, e.g. bank charges, rent, a loan received.
	Saved as a draft."""
	if direction not in ("Receive", "Pay"):
		frappe.throw(_("Choose money in or money out"))
	amount = flt(amount)
	if amount <= 0:
		frappe.throw(_("Enter the amount"))
	company = _company()
	kind = frappe.db.get_value("Account", {"name": money_account, "company": company, "is_group": 0}, "account_type")
	if kind not in ("Bank", "Cash"):
		frappe.throw(_("Choose the bank or cash account the money went through"))
	posting_date = posting_date or nowdate()
	if kind == "Bank" and not reference_no:
		frappe.throw(_("Enter the cheque / transfer reference for a bank voucher"))
	if isinstance(invoices, str):
		invoices = frappe.parse_json(invoices) if invoices.strip() else []
	if party:
		return _party_voucher(direction, company, money_account, amount, party_type, party, posting_date,
		                      mode_of_payment, reference_no, reference_date, remark, invoices or [])
	if not account:
		frappe.throw(_("Choose a customer / supplier, or the account the money is for"))
	return _journal_voucher(direction, company, money_account, kind, amount, account, posting_date,
	                        reference_no, reference_date, remark)


def _party_voucher(direction, company, money_account, amount, party_type, party, posting_date,
                   mode_of_payment, reference_no, reference_date, remark, invoices):
	from erpnext.accounts.party import get_party_account

	_check_party(party_type, party)
	frappe.has_permission("Payment Entry", "create", throw=True)
	party_account = get_party_account(party_type, party, company)
	pe = frappe.new_doc("Payment Entry")
	pe.update({
		"payment_type": direction, "company": company, "posting_date": posting_date,
		"party_type": party_type, "party": party, "mode_of_payment": mode_of_payment or None,
		"paid_amount": amount, "received_amount": amount, "source_exchange_rate": 1, "target_exchange_rate": 1,
		"remarks": remark or None,
	})
	if direction == "Receive":
		pe.paid_from, pe.paid_to = party_account, money_account
	else:
		pe.paid_from, pe.paid_to = money_account, party_account
	pe.paid_from_account_currency = pe.paid_to_account_currency = _currency(company)
	# Allocate: the invoices ticked, else oldest first. A Supplier being refunded
	# (Receive) or a Customer refunded (Pay) is not allocated: it stays on account.
	natural = (party_type == "Customer" and direction == "Receive") or (party_type == "Supplier" and direction == "Pay")
	if natural:
		left = amount
		open_ = open_invoices(party_type, party)
		if invoices:
			open_ = [i for i in open_ if i.name in invoices]
		dt = "Sales Invoice" if party_type == "Customer" else "Purchase Invoice"
		for inv in open_:
			if left <= 0:
				break
			take = min(left, flt(inv.outstanding_amount))
			pe.append("references", {"reference_doctype": dt, "reference_name": inv.name, "due_date": inv.due_date,
			                         "total_amount": flt(inv.grand_total), "outstanding_amount": flt(inv.outstanding_amount),
			                         "allocated_amount": take})
			left -= take
	set_payment_reference(pe, reference_no or pe.name, reference_no, reference_date or posting_date)
	pe.setup_party_account_field()
	pe.set_missing_values()
	pe.set_amounts()
	pe.flags.ignore_permissions = True
	pe.insert(ignore_permissions=True)
	frappe.db.commit()
	return {"name": pe.name, "doctype": "Payment Entry", "unallocated": flt(pe.unallocated_amount)}


def _journal_voucher(direction, company, money_account, kind, amount, account, posting_date,
                     reference_no, reference_date, remark):
	frappe.has_permission("Journal Entry", "create", throw=True)
	if not frappe.db.get_value("Account", {"name": account, "company": company, "is_group": 0}):
		frappe.throw(_("Choose the account the money is for"))
	if frappe.db.get_value("Account", account, "account_type") in ("Receivable", "Payable"):
		frappe.throw(_("{0} needs a customer or supplier: choose the party instead of the account").format(account))
	je = frappe.new_doc("Journal Entry")
	je.update({"voucher_type": "Bank Entry" if kind == "Bank" else "Cash Entry", "company": company,
	           "posting_date": posting_date, "cheque_no": reference_no or None,
	           "cheque_date": (reference_date or posting_date) if reference_no else None, "user_remark": remark or None})
	money = {"account": money_account, "debit_in_account_currency": amount if direction == "Receive" else 0,
	         "credit_in_account_currency": amount if direction == "Pay" else 0}
	other = {"account": account, "debit_in_account_currency": amount if direction == "Pay" else 0,
	         "credit_in_account_currency": amount if direction == "Receive" else 0}
	cc = frappe.get_cached_value("Company", company, "cost_center")
	for line in (other, money) if direction == "Pay" else (money, other):
		if frappe.db.get_value("Account", line["account"], "report_type") == "Profit and Loss":
			line["cost_center"] = cc
		je.append("accounts", line)
	je.flags.ignore_permissions = True
	je.insert(ignore_permissions=True)
	frappe.db.commit()
	return {"name": je.name, "doctype": "Journal Entry"}


@frappe.whitelist()
def list_vouchers(limit=300):
	"""Payment Entries plus Bank / Cash journals, newest first, named the way the
	reader knows them (Bank Receipt, Bank Payment, Cash Receipt, ...)."""
	frappe.has_permission("Payment Entry", "read", throw=True)
	out = []
	for p in frappe.get_all("Payment Entry", fields=["name", "payment_type", "party_type", "party", "party_name", "posting_date",
	                                                 "paid_amount", "received_amount", "unallocated_amount", "paid_from", "paid_to",
	                                                 "reference_no", "mode_of_payment", "status", "docstatus"],
	                        order_by="posting_date desc, creation desc", limit_page_length=cint(limit)):
		out.append({"doctype": "Payment Entry", "name": p.name, "kind": _pe_label(p), "posting_date": p.posting_date,
		            "party": p.party_name or p.party or "", "party_type": p.party_type,
		            "through": p.paid_to if p.payment_type == "Receive" else p.paid_from,
		            "amount": flt(p.received_amount if p.payment_type == "Receive" else p.paid_amount),
		            "unallocated": flt(p.unallocated_amount), "reference_no": p.reference_no or "",
		            "direction": p.payment_type, "status": p.status, "docstatus": p.docstatus})
	for j in frappe.get_all("Journal Entry", filters={"voucher_type": ["in", ["Bank Entry", "Cash Entry"]]},
	                        fields=["name", "voucher_type", "posting_date", "total_debit", "cheque_no", "user_remark", "docstatus"],
	                        order_by="posting_date desc, creation desc", limit_page_length=cint(limit)):
		lines = frappe.get_all("Journal Entry Account", filters={"parent": j.name},
		                       fields=["account", "debit_in_account_currency", "credit_in_account_currency"], order_by="idx")
		money_types = ("Bank", "Cash")
		money = next((l for l in lines if frappe.db.get_value("Account", l.account, "account_type") in money_types), None)
		other = ", ".join(l.account for l in lines if l is not money)
		direction = "Receive" if money and flt(money.debit_in_account_currency) else "Pay"
		word = "Bank" if j.voucher_type == "Bank Entry" else "Cash"
		out.append({"doctype": "Journal Entry", "name": j.name, "kind": word + (" Receipt" if direction == "Receive" else " Payment"),
		            "posting_date": j.posting_date, "party": other, "party_type": "Account",
		            "through": money.account if money else "", "amount": flt(j.total_debit), "unallocated": 0,
		            "reference_no": j.cheque_no or "", "direction": direction,
		            "status": {0: "Draft", 1: "Submitted", 2: "Cancelled"}[j.docstatus], "docstatus": j.docstatus,
		            "remark": j.user_remark or ""})
	out.sort(key=lambda r: (str(r["posting_date"]), r["name"]), reverse=True)
	return out


@frappe.whitelist()
def submit_voucher(doctype, name):
	from a3_trading_management.api._common import submit_document

	if doctype not in ("Payment Entry", "Journal Entry", "Sales Invoice", "Purchase Invoice"):
		frappe.throw(_("Not a voucher"))
	return submit_document(doctype, name)


# ---------------------------------------------------------------------------
# Credit Note (customer) and Debit Note (supplier), against an invoice
# ---------------------------------------------------------------------------

@frappe.whitelist()
def invoice_lines(doctype, name):
	"""The lines of a posted invoice a note can be raised against, with what has
	already been returned on earlier notes."""
	if doctype not in ("Sales Invoice", "Purchase Invoice"):
		frappe.throw(_("Choose an invoice"))
	frappe.has_permission(doctype, "read", doc=name, throw=True)
	inv = frappe.get_doc(doctype, name)
	if inv.docstatus != 1 or inv.is_return:
		frappe.throw(_("{0} is not a posted invoice").format(name))
	field = "sales_invoice_item" if doctype == "Sales Invoice" else "purchase_invoice_item"
	returned = {}
	for r in frappe.db.sql(f"""select i.{field} as row, sum(i.qty) as qty from `tab{doctype} Item` i
		join `tab{doctype}` p on p.name = i.parent
		where p.docstatus = 1 and p.is_return = 1 and p.return_against = %s group by i.{field}""", name, as_dict=True):
		returned[r.row] = -flt(r.qty)
	party = inv.customer if doctype == "Sales Invoice" else inv.supplier
	return {"name": inv.name, "party": party, "party_name": inv.get("customer_name") or inv.get("supplier_name"),
	        "posting_date": inv.posting_date, "grand_total": inv.grand_total, "outstanding_amount": inv.outstanding_amount,
	        "lines": [{"name": i.name, "item_code": i.item_code, "item_name": i.item_name, "qty": flt(i.qty), "uom": i.uom,
	                   "rate": flt(i.rate), "returned": returned.get(i.name, 0)} for i in inv.items]}


@frappe.whitelist()
def create_note(doctype, against, lines, posting_date=None, reason=None):
	"""A Credit Note (doctype=Sales Invoice) or Debit Note (Purchase Invoice)
	against a posted invoice. `lines`: [{name: invoice row, qty, rate}] — qty is
	what is credited (goods returned, or 1 for a price allowance) and rate the
	value per unit (leave it as the invoice rate for a plain return). VAT follows
	the invoice. Saved as a draft."""
	if doctype not in ("Sales Invoice", "Purchase Invoice"):
		frappe.throw(_("Choose an invoice"))
	frappe.has_permission(doctype, "create", throw=True)
	if isinstance(lines, str):
		lines = frappe.parse_json(lines)
	wanted = {l.get("name"): l for l in lines or [] if flt(l.get("qty")) > 0}
	if not wanted:
		frappe.throw(_("Enter the quantity to credit on at least one line"))
	if doctype == "Sales Invoice":
		from erpnext.accounts.doctype.sales_invoice.sales_invoice import make_sales_return as make
		field = "sales_invoice_item"
	else:
		from erpnext.accounts.doctype.purchase_invoice.purchase_invoice import make_debit_note as make
		field = "purchase_invoice_item"
	note = make(against)
	keep = []
	for item in note.items:
		w = wanted.get(item.get(field))
		if not w:
			continue
		qty = flt(w.get("qty"))
		item.qty = -qty
		item.stock_qty = -qty * flt(item.conversion_factor or 1)
		# A bill that took the stock in (update_stock) checks received = accepted +
		# rejected on every line; the mapped return still carries the full line.
		if item.meta.has_field("received_qty"):
			item.received_qty = -qty
			item.rejected_qty = 0
		# Fix the rate as given (or the invoice's own rate). ERPNext otherwise rebuilds
		# it from price list + margin + discount: an invoice priced by hand above its
		# price list carries that gap as a margin, which was added on top again
		# (500 on the bill came back as 980.56 on the note).
		rate = flt(w.get("rate")) if w.get("rate") not in (None, "") else flt(item.rate)
		item.rate = rate
		item.price_list_rate = rate
		item.discount_percentage = 0
		item.discount_amount = 0
		item.margin_type = None
		item.margin_rate_or_amount = 0
		item.rate_with_margin = 0
		keep.append(item)
	if not keep:
		frappe.throw(_("None of the lines given are on {0}").format(against))
	note.set("items", keep)
	for i, item in enumerate(note.items, 1):
		item.idx = i
	note.posting_date = posting_date or nowdate()
	note.set_posting_time = 1
	# The note reduces what is due on the invoice it is against (ERPNext's default
	# keeps it as a separate open credit, which then ages on its own).
	if note.meta.has_field("update_outstanding_for_self"):
		note.update_outstanding_for_self = 0
	if doctype == "Sales Invoice":
		note.remarks = reason or note.remarks
	else:
		note.remarks = reason or note.remarks
		# The supplier's own debit note number: none yet; keep the bill's for reference.
		note.bill_no = note.bill_no or against
	note.calculate_taxes_and_totals()
	note.flags.ignore_permissions = True
	note.insert(ignore_permissions=True)
	frappe.db.commit()
	return {"name": note.name, "doctype": doctype, "grand_total": note.grand_total}


@frappe.whitelist()
def list_notes(limit=300):
	frappe.has_permission("Sales Invoice", "read", throw=True)
	out = []
	for dt, kind, party_field, name_field in (("Sales Invoice", "Credit Note", "customer", "customer_name"),
	                                         ("Purchase Invoice", "Debit Note", "supplier", "supplier_name")):
		for r in frappe.get_all(dt, filters={"is_return": 1}, fields=["name", "posting_date", party_field, name_field, "return_against",
		                                                              "grand_total", "status", "docstatus", "remarks"],
		                        order_by="posting_date desc, creation desc", limit_page_length=cint(limit)):
			out.append({"doctype": dt, "kind": kind, "name": r.name, "posting_date": r.posting_date,
			            "party": r.get(name_field) or r.get(party_field), "against": r.return_against,
			            "amount": abs(flt(r.grand_total)), "status": r.status, "docstatus": r.docstatus,
			            "remarks": (r.remarks or "") if (r.remarks or "") != "No Remarks" else ""})
	out.sort(key=lambda r: (str(r["posting_date"]), r["name"]), reverse=True)
	return out


@frappe.whitelist()
def search_invoices(doctype, search=None):
	"""Posted, non-return invoices for the note picker."""
	if doctype not in ("Sales Invoice", "Purchase Invoice"):
		frappe.throw(_("Choose an invoice"))
	frappe.has_permission(doctype, "read", throw=True)
	pf, nf = ("customer", "customer_name") if doctype == "Sales Invoice" else ("supplier", "supplier_name")
	like = "%{0}%".format((search or "").strip())
	return frappe.get_all(doctype, filters={"docstatus": 1, "is_return": 0},
	                      or_filters={"name": ["like", like], pf: ["like", like], nf: ["like", like]},
	                      fields=["name", pf + " as party", nf + " as party_name", "posting_date", "grand_total"],
	                      order_by="posting_date desc", limit_page_length=15)


# ---------------------------------------------------------------------------
# Bilingual prints (PDF): statement, receivable / payable, ledger
# ---------------------------------------------------------------------------

_FONT_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "public", "fonts")


def _font_css():
	"""Arabic font embedded as a data URI, so the PDF has the glyphs on a server
	without Arabic fonts. "src:  url(" (two spaces) keeps Frappe's scrub_urls off
	the rule (see setup/letter_head.py)."""
	css = []
	for weight, fname in ((400, "NotoNaskhArabic-Regular-ar.ttf"), (700, "NotoNaskhArabic-Bold-ar.ttf")):
		path = os.path.join(_FONT_DIR, fname)
		if os.path.exists(path):
			with open(path, "rb") as f:
				data = base64.b64encode(f.read()).decode()
			css.append("@font-face { font-family: 'RLI Arabic'; src:  url('data:font/truetype;base64,%s') format('truetype'); "
			           "font-weight: %d; font-style: normal; }" % (data, weight))
	return "\n".join(css)


def _render_pdf(mode, data, filename):
	from frappe.utils.pdf import get_pdf

	lh = frappe.db.get_value("Letter Head", {"is_default": 1}, ["content", "footer"], as_dict=True) or {}
	html = frappe.render_template("templates/print/rli_statement.html", {
		"mode": mode, "d": data, "letter_head": lh.get("content") or "", "footer": lh.get("footer") or "",
		"font_css": _font_css(), "printed_on": now_datetime().strftime("%d-%m-%Y %H:%M"),
		"printed_by": frappe.db.get_value("User", frappe.session.user, "full_name") or frappe.session.user,
		"fmt_date": lambda v: formatdate(v, "dd-MM-yyyy") if v else "",
	})
	if frappe.flags.statement_as_html:   # tests / previews
		return html
	pdf = get_pdf(html, {"margin-top": "42mm", "margin-bottom": "18mm", "margin-left": "10mm", "margin-right": "10mm",
	                     "page-size": "A4", "orientation": "Landscape" if mode == "receivable" else "Portrait"})
	frappe.local.response.filename = filename
	frappe.local.response.filecontent = pdf
	frappe.local.response.type = "pdf"


@frappe.whitelist()
def statement_pdf(party_type, party, from_date=None, to_date=None):
	d = statement_of_account(party_type, party, from_date, to_date)
	_render_pdf("statement", d, "Statement-of-Account-%s.pdf" % frappe.scrub(d["party_name"])[:40])


@frappe.whitelist()
def receivable_pdf(party_type="Customer", report_date=None):
	d = receivable_summary(party_type, report_date)
	_render_pdf("receivable", d, "Accounts-%s-%s.pdf" % ("Receivable" if party_type == "Customer" else "Payable", d["report_date"]))


@frappe.whitelist()
def ledger_pdf(account, from_date=None, to_date=None, party_type=None, party=None):
	d = general_ledger(account, from_date, to_date, party_type, party)
	_render_pdf("ledger", d, "Ledger-%s.pdf" % frappe.scrub(d["account_name"])[:40])
