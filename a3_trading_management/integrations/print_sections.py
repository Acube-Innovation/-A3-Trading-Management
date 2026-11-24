"""Print Sections (terms, technical specification, warranty...): each row of a
Quotation / Sales Order prints on a page of its own (templates/print/rli_document.html).

ERPNext's Quotation -> Sales Order mapping copies only the tables it knows, so a
Sales Order made from a quotation picks its sections up here."""

import re

import frappe

_UI = '<span class="ql-ui" contenteditable="false"></span>'
_P = re.compile(r"<p\b[^>]*>(.*?)</p>", re.S)
_LEAD = r"(?:\s|&nbsp;|<br>)*"


def tidy_list_html(html):
	"""A numbered list continued by hand ("a) ..." lines, then "11. ..." lines typed
	as plain paragraphs after it) prints at three different indents. Fold those
	paragraphs into the list: "a) x" -> a sub-item (lettered by the list itself),
	"11. x" -> the next item. Also drops stray spaces at the start of list items."""
	if not html or "</ol>" not in html:
		return html
	out, pos = [], 0
	for m in re.finditer(r"</ol>", html):
		out.append(html[pos:m.start()])
		j, moved = m.end(), []
		# Only a numbered list is continued; "10. Heading" after a bullet list stays.
		last_li = html.rfind("<li", pos, m.start())
		numbered = last_li >= 0 and 'data-list="ordered"' in html[last_li:html.find(">", last_li)]
		while numbered:
			p = _P.match(html, j + len(html[j:]) - len(html[j:].lstrip()))
			if not p:
				break
			inner = p.group(1)
			lead = re.match(r"^%s((?:<(?:strong|em|b|u)>)*)%s([a-z]\)|\d{1,2}[.)])\s*" % (_LEAD, _LEAD), inner)
			if not lead:
				break
			body = lead.group(1) + inner[lead.end():]
			sub = lead.group(2)[0].isalpha()
			moved.append('<li data-list="ordered"%s>%s%s</li>' % (' class="ql-indent-1"' if sub else "", _UI, body.strip()))
			j = p.end()
		out.append("".join(moved) + "</ol>")
		pos = j
	out.append(html[pos:])
	html = "".join(out)
	# " King Pin" -> "King Pin": leading spaces inside list items.
	return re.sub(r"(<li\b[^>]*>%s(?:<(?:strong|em|b|u)>)*)(?:\s|&nbsp;)+" % re.escape(_UI), r"\1", html)


def fill_from_templates(doc, method=None):
	"""Quotation / Sales Order validate: a row with a template but no text yet (made in
	code, or the text cleared) takes the template's title and text. Rows already
	filled are left alone, so text changed for one document is never overwritten
	(the Desk form fills a row as soon as its template is picked)."""
	for row in doc.get("custom_print_sections") or []:
		if not row.terms_template:
			continue
		if not row.content or not row.title:
			title, terms = frappe.db.get_value("Terms and Conditions", row.terms_template,
			                                   ["custom_print_title", "terms"]) or (None, None)
			if not row.title:
				row.title = title or row.terms_template
			if not row.content:
				row.content = terms


def copy_from_quotation(doc, method=None):
	"""Sales Order before_insert: no sections of its own -> the quotation's."""
	if doc.get("custom_print_sections"):
		return
	quotations = []
	for item in doc.get("items") or []:
		q = item.get("prevdoc_docname")
		if q and q not in quotations and frappe.db.exists("Quotation", q):
			quotations.append(q)
	for q in quotations[:1]:
		for row in frappe.get_all("Print Section", filters={"parent": q, "parenttype": "Quotation"},
		                          fields=["terms_template", "title", "content"], order_by="idx"):
			doc.append("custom_print_sections", row)


# --- 24 Nov 2026 (user): one heading per page, one line per numbered point ---------

_BLOCK = re.compile(r"<(p|h[1-6]|ol|ul|div)\b[^>]*>.*?</\1>", re.S)
_NUMBERED = re.compile(r"^\s*\d{1,2}\s*[.)](?!\d)\s*\S")  # "1. x", not "2.5 inch"


def _text(html):
	return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", (html or "").replace("&nbsp;", " ").replace("&amp;", "&"))).strip()


def _norm(s):
	return re.sub(r"[^a-z0-9]+", "", (s or "").lower())


def _split_wrap(html):
	"""('<div class="ql-editor ...">', inner, '</div>') or ('', html, '')."""
	m = re.match(r'^\s*(<div class="ql-editor[^"]*">)(.*)(</div>)\s*$', html or "", re.S)
	return (m.group(1), m.group(2), m.group(3)) if m else ("", html or "", "")


def lift_heading(title, html):
	"""The page title prints above the text, so a heading at the very top of the text
	is either the same words (dropped) or a sub-heading such as "40 FEET SKELETAL
	SEMI-TRAILER" (joined to the title: one heading line)."""
	head, inner, tail = _split_wrap(html)
	m = re.match(r"\s*<(h[1-6]|p)\b[^>]*>(.*?)</\1>", inner, re.S)
	if not m:
		return title, html
	words = _text(m.group(2))
	is_heading = m.group(1).startswith("h") or (words and words == words.upper() and len(words) < 80 and not _NUMBERED.match(words))
	if not words or not is_heading:
		return title, html
	if _norm(words) == _norm(title) or _norm(words) in _norm(title):
		pass  # the same heading again
	elif title:
		title = "%s %s" % (title.strip(), words)
	else:
		title = words
	return title, head + inner[m.end():] + tail


def join_numbered_points(html):
	"""'1. Payload Capacity:' on one line and '40 Tons.' on the next -> one line:
	'1. Payload Capacity: 40 Tons.' Plain lines under a numbered point are joined to
	it (a long one wraps by itself); lists and the next numbered point start anew."""
	head, inner, tail = _split_wrap(html)
	blocks, pos, out = [], 0, []
	for m in _BLOCK.finditer(inner):
		if inner[pos:m.start()].strip():
			return html  # loose text between blocks: not the editor's layout, leave it
		blocks.append((m.group(1), m.group(0)))
		pos = m.end()
	if not blocks or inner[pos:].strip():
		return html
	i = 0
	while i < len(blocks):
		tag, b = blocks[i]
		if tag == "p" and _NUMBERED.match(_text(b)) and _text(b).endswith(":"):
			body = re.sub(r"(?:<br>|\s)+</p>$", "</p>", b)
			j = i + 1
			while j < len(blocks) and blocks[j][0] == "p" and _text(blocks[j][1]) and not _NUMBERED.match(_text(blocks[j][1])):
				extra = re.sub(r"^<p\b[^>]*>|</p>$", "", blocks[j][1])
				extra = re.sub(r"(?:<br>|\s)+$", "", re.sub(r"<br>", " ", extra)).strip()
				body = body[:-4].rstrip() + " " + extra + "</p>"
				j += 1
			out.append(body)
			i = j
			continue
		out.append(b)
		i += 1
	return head + "".join(out) + tail


def tidy_section(title, html):
	"""Everything a section's text needs before it prints well; idempotent."""
	title, html = lift_heading(title, html)
	html = join_numbered_points(tidy_list_html(html))
	return title, html
