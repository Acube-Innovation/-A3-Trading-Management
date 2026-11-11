"""Cut Shape: a shape a sheet piece can be cut in, set up in Desk.

Each shape lists the sizes the BOM's Cut size window asks for (Diameter for a
circle, Base and Height for a triangle...) and two formulas giving the rectangle
the shape is cut and charged as. Sheets are only ever cut as rectangles; the
part of that rectangle outside the shape is waste."""

import math
import re

import frappe
from frappe import _
from frappe.model.document import Document

KEY = re.compile(r"^[a-z][a-z0-9_]*$")
NAMES = {"max": max, "min": min, "sqrt": math.sqrt, "pi": math.pi, "abs": abs, "round": round}


def evaluate(formula, values):
	"""A formula over the sizes, safely: names, numbers and the few helpers above."""
	return float(frappe.safe_eval(formula or "0", eval_globals=None, eval_locals={**NAMES, **values}))


class CutShape(Document):
	def validate(self):
		if not self.dimensions:
			frappe.throw(_("Add at least one size to ask for"))
		seen = set()
		for d in self.dimensions:
			if not (d.key or "").strip():
				d.key = re.sub(r"[^a-z0-9_]+", "_", (d.label or "").strip().lower()).strip("_")
			d.key = d.key.strip()
			if not KEY.match(d.key) or d.key in NAMES:
				frappe.throw(_("Row {0}: name in formula “{1}” must be lower-case letters, digits or _, start with a letter, and not be {2}")
				             .format(d.idx, d.key, ", ".join(NAMES)))
			if d.key in seen:
				frappe.throw(_("Row {0}: “{1}” is used twice").format(d.idx, d.key))
			seen.add(d.key)
		# Try the formulas with every size = 10 cm: they must give a positive size.
		sample = {k: 10.0 for k in seen}
		for field in ("length_formula", "width_formula"):
			try:
				value = evaluate(self.get(field), sample)
			except Exception as e:
				frappe.throw(_("{0}: {1}").format(self.meta.get_label(field), e))
			if value <= 0:
				frappe.throw(_("{0} gives {1} when every size is 10; it must be more than 0").format(self.meta.get_label(field), value))
