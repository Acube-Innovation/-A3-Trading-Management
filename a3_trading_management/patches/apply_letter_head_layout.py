"""11 Nov 2026: the letter head layout after the client's sample (name, address,
P.O. Box / TEL / FAX, email and TRN on the right; empty footer). setup() only
creates the record when it is missing, so an existing one is updated here."""

import frappe

from a3_trading_management.setup.letter_head import NAME, apply_layout


def execute():
	if frappe.db.exists("Letter Head", NAME):
		apply_layout()
