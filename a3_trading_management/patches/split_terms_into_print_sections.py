"""24 Nov 2026 (user): terms and the technical specification print on pages of their
own (Print Sections). Until now one Terms and Conditions text held both, the spec
starting at a "TECHNICAL SPECIFICATION(S) ..." line.

- Each such template is cut in two: the template keeps the terms (Print Title
  "TERMS & CONDITIONS"); a new "Technical Specification<suffix>" template gets the
  spec, titled with the document's own heading line.
- Each open Quotation / Sales Order whose own terms hold a spec gets two Print
  Section rows cut from ITS text (so wording changed for that document is kept),
  and its single Terms box is cleared. Cancelled documents are left as they were.
Runs once per site; documents that already have Print Sections are skipped."""

import re

import frappe

HEADING = "TECHNICAL SPECIFICATION"
WRAP = '<div class="ql-editor read-mode">'


def split(html):
	"""(terms_html, spec_html, heading_text) or None when there is no spec heading."""
	if not html:
		return None
	i = html.upper().find(HEADING)
	if i < 0:
		return None
	# The block element holding the heading: <p>, <h1>..<h6>, <div> or <li>.
	start, tag = -1, None
	for m in re.finditer(r"<(p|h[1-6]|div|li)\b[^>]*>", html[:i]):
		if m.group(1) == "div" and "ql-editor" in m.group(0):
			continue
		start, tag = m.start(), m.group(1)
	if start < 0:
		return None
	close = html.find("</%s>" % tag, i)
	if close < 0:
		return None
	end = close + len("</%s>" % tag)
	heading = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html[start:end])).strip()
	terms, spec = html[:start], html[end:]
	wrapped = html.lstrip().startswith(WRAP)
	if wrapped:
		terms = terms.rstrip() + "</div>"
		spec = WRAP + spec
	return terms, spec, heading


def spec_template_name(name):
	suffix = name.split("Terms", 1)[1] if "Terms" in name else "-" + name
	return "Technical Specification" + suffix


def execute():
	if not frappe.db.exists("DocType", "Print Section"):
		return
	# Patches run before after_migrate creates the custom fields: make them now, or
	# on a site migrating for the first time there is no Print Title / Print Sections.
	from a3_trading_management.setup.install import setup_custom_fields

	setup_custom_fields()
	frappe.clear_cache(doctype="Terms and Conditions")
	for dt in ("Quotation", "Sales Order"):
		frappe.clear_cache(doctype=dt)
	pairs = {}  # old template -> spec template
	for t in frappe.get_all("Terms and Conditions", fields=["name", "terms"]):
		cut = split(t.terms)
		if not cut:
			continue
		terms, spec, heading = cut
		new = spec_template_name(t.name)
		if not frappe.db.exists("Terms and Conditions", new):
			doc = frappe.get_doc("Terms and Conditions", t.name)
			sd = frappe.copy_doc(doc)
			sd.title = new
			sd.terms = spec
			sd.custom_print_title = heading
			sd.insert(ignore_permissions=True)
			doc.terms = terms
			doc.custom_print_title = doc.get("custom_print_title") or "TERMS & CONDITIONS"
			doc.save(ignore_permissions=True)
		pairs[t.name] = new

	for dt in ("Quotation", "Sales Order"):
		if not frappe.get_meta(dt).has_field("custom_print_sections"):
			continue
		for d in frappe.get_all(dt, filters={"docstatus": ["<", 2]}, fields=["name", "terms", "tc_name"]):
			if frappe.db.exists("Print Section", {"parenttype": dt, "parent": d.name}):
				continue
			cut = split(d.terms)
			if not cut:
				continue
			terms, spec, heading = cut
			terms_tpl = d.tc_name if d.tc_name and frappe.db.exists("Terms and Conditions", d.tc_name) else None
			spec_tpl = pairs.get(d.tc_name)
			for idx, (tpl, title, content) in enumerate(((terms_tpl, "TERMS & CONDITIONS", terms), (spec_tpl, heading, spec)), 1):
				row = frappe.get_doc({
					"doctype": "Print Section", "parenttype": dt, "parent": d.name, "parentfield": "custom_print_sections",
					"idx": idx, "terms_template": tpl, "title": title, "content": content,
				})
				row.db_insert()
			# The single box is emptied (and its template link with it, or ERPNext fills
			# it back) so the terms do not print twice.
			frappe.db.set_value(dt, d.name, {"terms": None, "tc_name": None}, update_modified=False)
