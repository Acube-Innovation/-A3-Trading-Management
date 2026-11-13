"""The Redlines letter head (from "letter head - SAMPLE - TRADING.docx"): logo on
the left, company name and address on the right, contact line in the footer.

A standard Letter Head record, so the portal prints (templates/print_base.html)
and Desk's /printview carry the same header. Created once; edits made to it in
Desk afterwards are kept."""

import base64
import os

import frappe

NAME = "Redlines Trading"
LOGO = "/assets/a3_trading_management/images/redlines-letterhead.jpg"
# Agency FB Bold, the logo's typeface (from the client's logo pack), cut down to basic
# Latin (U+0020-007E, ~23 KB) and embedded as a data URI. A linked /assets/ font does
# not work in PDFs: Frappe's scrub_urls appends "!important" after url(...), which
# breaks the @font-face rule. Its regex matches ":url(" with at most one space, so
# the "src:  url(" below (two spaces) is deliberate: it keeps scrub_urls off the rule. The full font would not fit
# either: Frappe copies the default letter head into a ~64 KB Text column (DefaultValue.defvalue).
FONT_FILE = os.path.join(os.path.dirname(os.path.dirname(__file__)), "public", "fonts", "AgencyFB-Bold-latin.ttf")
with open(FONT_FILE, "rb") as f:
	FONT_URL = "data:font/truetype;base64," + base64.b64encode(f.read()).decode()
FONT = "'Redlines Agency FB', 'Agency FB', 'Arial Narrow', Arial, sans-serif"

TRN = "105503411800003"
# Layout after the client's letter head sample (12 Nov 2026): logo on the left; on
# the right, in the logo's typeface: name, P.O. Box, TEL / FAX / email, and the TRN
# in the logo's red.
LINE = f"font-family:{FONT};font-weight:700;letter-spacing:.4px;line-height:1.25"
CONTENT = f"""<style>@font-face {{ font-family: 'Redlines Agency FB'; src:  url('{FONT_URL}') format('truetype'); font-weight: 700; font-style: normal; }}</style>
<table style="width:100%;border-collapse:collapse;border:0">
	<tr>
		<td style="width:190px;vertical-align:middle;padding:0;border:0">
			<img src="{LOGO}" alt="Redlines International" style="height:82px;width:auto">
		</td>
		<td style="vertical-align:middle;text-align:right;padding:0;border:0">
			<div style="{LINE};font-size:30px;line-height:1.1">
				<span style="color:#FF0066">RED</span><span style="color:#00268A">LINES INTERNATIONAL TRADING LLC</span>
			</div>
			<div style="{LINE};font-size:15px;color:#1a1a1a;margin-top:6px">P.O.BOX 27969,DUBAI,UAE</div>
			<div style="{LINE};font-size:15px;color:#1a1a1a;margin-top:2px">TEL: +971 4 3558595, 3596160 &nbsp;&nbsp; FAX: +971 4 3596181 &nbsp;&nbsp; <span style="color:#00268A">info@</span><span style="color:#EC2E5D">red</span><span style="color:#00268A">lines.ae</span></div>
			<div style="{LINE};font-size:15px;color:#EC2E5D;margin-top:2px">TRN : {TRN}</div>
		</td>
	</tr>
</table>
<div style="border-top:3px solid #EC2E5D;margin-top:8px"></div>"""

# The contact line moved up into the header, so the footer is empty.
FOOTER = ""


def setup():
	if not frappe.db.exists("Letter Head", NAME):
		frappe.get_doc({
			"doctype": "Letter Head",
			"letter_head_name": NAME,
			"source": "HTML",
			"content": CONTENT,
			"footer_source": "HTML",
			"footer": FOOTER,
			"is_default": 1,
		}).insert(ignore_permissions=True)
	# A company with no letter head of its own prints on this one.
	for company in frappe.get_all("Company", filters={"default_letter_head": ["in", ["", None]]}, pluck="name"):
		frappe.db.set_value("Company", company, "default_letter_head", NAME)


def apply_layout():
	"""Write the layout above over the existing letter head (run once, 11 Nov 2026)."""
	lh = frappe.get_doc("Letter Head", NAME)
	lh.source, lh.content = "HTML", CONTENT
	lh.footer_source, lh.footer = "HTML", FOOTER
	lh.save(ignore_permissions=True)
