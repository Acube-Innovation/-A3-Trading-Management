"""Number Items from a series while leaving Item Code a normal, typeable field.

Item Code stays mandatory and editable, and comes pre-filled with the next number
in the series ("RIG" -> RIG00001, RIG00002, ...). Accept it and the Item is
numbered; type over it and your own code is kept.

ERPNext's own "Item Naming By: Naming Series" mode cannot give that: Item.autoname
does `self.item_code = self.name` unconditionally, so anything typed is discarded,
and it hides the field. So Items stay in "Item Code" mode and before_naming() below
does the filling instead.

The client presets the field from next_item_code(), which is a PREVIEW — it reads
the counter without consuming it, so two people creating Items at once both see the
same number. before_naming() is what makes that safe: a code still matching the
series pattern is treated as "the preset, untouched" and re-allocated from the
counter at insert, so the second save gets the next number instead of colliding.
A code that does not match the pattern is left exactly as typed.
"""

import re

import frappe
from frappe.model.naming import NamingSeries, make_autoname, parse_naming_series


def _series() -> str | None:
	"""The Item series template, e.g. "RIG.#####".

	Read from Item's `naming_series` field (Customize Form, or Document Naming
	Settings) so the prefix and width stay editable in Desk, not hardcoded here. The
	field itself is hidden on the form — only its options matter.
	"""
	df = frappe.get_meta("Item").get_field("naming_series")
	series = (df.default or (df.options or "").split("\n")[0] or "").strip()
	# NamingSeries appends ".#####" when the template carries no number part, which
	# is what set_name_by_naming_series() would have done with it.
	return NamingSeries(series).series if series else None


def _is_series_code(code: str, series: str) -> bool:
	"""True if `code` is what this series generates, i.e. an untouched preset."""
	ns = NamingSeries(series)
	# get_prefix() resolves any date parts, so this matches the codes the series is
	# handing out now rather than every code it ever produced.
	pattern = re.escape(ns.get_prefix()) + rf"\d{{{ns.series.count('#')}}}"
	return bool(re.fullmatch(pattern, code))


@frappe.whitelist()
def next_item_code() -> str | None:
	"""Next code the series would hand out. Display only — nothing is consumed."""
	if not frappe.has_permission("Item", "create"):
		return None

	series = _series()
	if not series:
		return None

	def peek(prefix: str, digits: int) -> str:
		# Deliberately no for_update: this must not take a row lock on the counter
		# that real inserts are queueing on.
		current = frappe.db.get_value("Series", prefix, "current", order_by="name") or 0
		return str(int(current) + 1).zfill(digits)

	try:
		return parse_naming_series(series, number_generator=peek)
	except Exception:
		# e.g. a series carrying a fieldname part, which needs a document to resolve.
		# Items still save fine; the field just comes up empty.
		frappe.clear_last_message()
		return None


def before_naming(doc, method=None):
	"""Fill Item Code from the series unless a code of the user's own was typed.

	Runs on before_naming rather than autoname because Item.autoname derives `name`
	from item_code — the field has to be populated before that point.
	"""
	# Variants take their code from the template's attributes (make_variant_item_code).
	if doc.variant_of:
		return

	# In ERPNext's series mode Item.autoname allocates the number itself; doing it
	# here as well would burn two per Item.
	if frappe.db.get_default("item_naming_by") == "Naming Series":
		return

	series = _series()
	if not series:
		return

	code = (doc.item_code or "").strip()
	if code and not _is_series_code(code, series):
		# A hand-picked code (PRT-OILF, a supplier SKU, _ensure_service_item's label).
		return

	# Blank, or the preset still untouched: take the real next number off the counter.
	doc.item_code = make_autoname(series, doc.doctype, doc)
