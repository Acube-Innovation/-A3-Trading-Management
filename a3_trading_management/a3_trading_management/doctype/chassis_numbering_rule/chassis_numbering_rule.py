# Implements: the chassis numbering rule A3 stamps on every trailer (task 7).
"""Chassis Numbering Rule — format, prefix, check digit and starting number.

Redlines supplies the specification; this doctype holds it rather than hard-coding
it, because the exact format was still to be confirmed when Phase 2 was built and a
guessed format embossed onto a trailer is not something you can take back.

`issue_next()` is the only way a chassis number is minted. It refuses to issue
until `confirmed_by_redlines` is ticked, and it takes the row lock before reading
`next_number` so two work orders submitted at the same moment cannot be handed the
same chassis number.
"""

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import nowdate, getdate


class ChassisNumberingRule(Document):
	def validate(self):
		if self.sequence_length is not None and not (1 <= self.sequence_length <= 18):
			frappe.throw(_("Sequence Length must be between 1 and 18"))
		if self.next_number is not None and self.next_number < 0:
			frappe.throw(_("Next Number cannot be negative"))
		self.sample_number = self.format_number(self.next_number or 1)

	# -- format -------------------------------------------------------------

	def format_number(self, number):
		"""Render `number` under the current settings. Pure — issues nothing."""
		sep = self.separator or ""
		parts = []
		if self.prefix:
			parts.append(self.prefix.strip())
		if self.include_year:
			year = getdate(nowdate()).year
			parts.append(str(year) if self.year_format == "YYYY" else f"{year % 100:02d}")

		body = str(int(number)).zfill(self.sequence_length or 5)
		check = self._check_digit(("".join(parts) + body) if parts else body)
		parts.append(body + (str(check) if check is not None else ""))
		return sep.join(parts)

	def _check_digit(self, value):
		method = self.check_digit_method or "None"
		if method == "None":
			return None
		digits = [int(c) for c in str(value) if c.isdigit()]
		if not digits:
			return None
		if method == "Mod 10 (Luhn)":
			total = 0
			# Luhn doubles every second digit counting from the right of the
			# payload, i.e. the position the check digit itself will occupy is 0.
			for i, d in enumerate(reversed(digits)):
				if i % 2 == 0:
					d *= 2
					if d > 9:
						d -= 9
				total += d
			return (10 - total % 10) % 10
		if method == "Mod 11":
			# Weights 2..7 repeating from the right; remainder 10 collapses to 0
			# so the result is always a single character.
			total = sum(d * (2 + i % 6) for i, d in enumerate(reversed(digits)))
			return (11 - total % 11) % 11 % 10
		return None

	# -- issue --------------------------------------------------------------

	def issue_next(self):
		"""Mint the next chassis number and advance the counter.

		Takes the row lock first: two work orders submitted together would
		otherwise both read the same `next_number` and be stamped alike.
		"""
		if not self.confirmed_by_redlines:
			frappe.throw(
				_("The chassis numbering rule has not been confirmed by Redlines yet. "
				  "Confirm the format in Chassis Numbering Rule before issuing chassis numbers.")
			)
		# tabSingles is (doctype, field, value) — there is no `name` column. Lock the
		# counter's own row so two concurrent work orders cannot read it alike.
		frappe.db.sql(
			"select value from `tabSingles` where doctype=%s and field='next_number' for update",
			self.doctype,
		)
		current = frappe.db.get_single_value("Chassis Numbering Rule", "next_number") or 1
		number = self.format_number(current)
		frappe.db.set_value("Chassis Numbering Rule", None, "next_number", int(current) + 1)
		return number


def get_rule():
	return frappe.get_single("Chassis Numbering Rule")


@frappe.whitelist()
def preview(number=None):
	"""Preview a number without issuing it — used by the Desk form."""
	rule = get_rule()
	return rule.format_number(number or rule.next_number or 1)
