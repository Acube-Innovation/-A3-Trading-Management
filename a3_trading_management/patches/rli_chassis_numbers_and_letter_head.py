"""Redlines' confirmed formats (4 Nov 2026).

* Chassis numbers: RLI + year + TR + five digits, the first being RLI2026TR00100
  (as on the plates). Only applied while the rule is still unconfirmed, so a
  rule already issuing numbers is never reset.
* Letter head: the company name is one word, REDLINES. The letter head is
  created once, so its stored content is brought up to date here.
"""

import frappe


def execute():
	rule = frappe.get_single("Chassis Numbering Rule")
	if not rule.confirmed_by_redlines:
		rule.update({
			"prefix": "RLI",
			"include_year": 1,
			"year_format": "YYYY",
			"text_after_year": "TR",
			"separator": "",
			"sequence_length": 5,
			"next_number": 100,
			"check_digit_method": "None",
			"confirmed_by_redlines": 1,
		})
		rule.flags.ignore_permissions = True
		rule.save()

	from a3_trading_management.setup.letter_head import CONTENT, NAME

	if frappe.db.exists("Letter Head", NAME):
		frappe.db.set_value("Letter Head", NAME, "content", CONTENT)
