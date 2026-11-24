"""Helpers for the RLI print formats, exposed to Jinja through hooks.jinja."""
import base64
import os

from markupsafe import Markup

_STAMP = None


def rli_stamp(height=92, style=""):
	"""The company stamp (Redlines International Trading L.L.C) as an <img> with the
	PNG inlined, so it shows in the browser preview and in the PDF alike without
	wkhtmltopdf having to fetch a URL. Transparent background: it sits over the
	signature line like a real stamp."""
	global _STAMP
	if _STAMP is None:
		path = os.path.join(os.path.dirname(__file__), "public", "images", "rli_stamp.png")
		with open(path, "rb") as f:
			_STAMP = base64.b64encode(f.read()).decode()
	return Markup(
		'<img class="rli-stamp" alt="Company stamp" src="data:image/png;base64,%s" style="height:%dpx;width:%dpx;%s">'
		% (_STAMP, int(height), int(height), style)
	)


def rli_section(title, html):
	"""[title, html] for a Print Section page, cleaned the way the tidy patch cleans
	saved texts, so a text typed later prints the same way: no repeated heading,
	one line per numbered point."""
	from a3_trading_management.integrations.print_sections import tidy_section

	title, html = tidy_section(title or "", html or "")
	return [title, Markup(html)]
