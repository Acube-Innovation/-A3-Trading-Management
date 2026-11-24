"""24 Nov 2026 (user): section texts print cleanly.
- The older spec continued its numbered list by hand ("a) ..." and "11. ..." typed
  as plain lines), so items printed at three different indents: one list now.
- A heading at the top of a text repeated the page title ("TERMS & CONDITIONS"
  twice) or was a second heading line ("40 FEET SKELETAL SEMI-TRAILER"): dropped,
  or joined to the title.
- "1. Payload Capacity:" and "40 Tons." on separate lines: one line now.
Applied to every Print Section and Terms and Conditions template. Idempotent."""

import frappe

from a3_trading_management.integrations.print_sections import tidy_section


def execute():
	tc_meta = frappe.get_meta("Terms and Conditions")
	has_title = tc_meta.has_field("custom_print_title")
	for r in frappe.get_all("Terms and Conditions", fields=["name", "terms"] + (["custom_print_title"] if has_title else [])):
		title, html = tidy_section(r.get("custom_print_title") or "", r.terms)
		values = {}
		if html != r.terms:
			values["terms"] = html
		if has_title and title and title != (r.get("custom_print_title") or ""):
			values["custom_print_title"] = title
		if values:
			frappe.db.set_value("Terms and Conditions", r.name, values, update_modified=False)
	if not frappe.db.exists("DocType", "Print Section"):
		return
	for r in frappe.get_all("Print Section", fields=["name", "title", "content"]):
		title, html = tidy_section(r.title or "", r.content)
		if (title, html) != (r.title or "", r.content):
			frappe.db.set_value("Print Section", r.name, {"title": title or r.title, "content": html}, update_modified=False)
