"""The Redlines letter head (from "letter head - SAMPLE - TRADING.docx"): logo on
the left, company name and address on the right, contact line in the footer.

A standard Letter Head record, so the portal prints (templates/print_base.html)
and Desk's /printview carry the same header. Created once; edits made to it in
Desk afterwards are kept."""

import frappe

NAME = "Redlines Trading"
LOGO = "/assets/a3_trading_management/images/redlines-letterhead.jpg"
FONT = "'Agency FB', 'Arial Narrow', Arial, sans-serif"

CONTENT = f"""<table style="width:100%;border-collapse:collapse;border:0">
	<tr>
		<td style="width:150px;vertical-align:middle;padding:0;border:0">
			<img src="{LOGO}" alt="Redlines International" style="height:60px;width:auto">
		</td>
		<td style="vertical-align:middle;text-align:right;padding:0;border:0;font-family:{FONT}">
			<div style="font-size:24px;font-weight:700;letter-spacing:.5px;line-height:1.15">
				<span style="color:#FF0066">RED</span><span style="color:#00268A">LINES INTERNATIONAL TRADING LLC</span>
			</div>
			<div style="font-size:14px;font-weight:700;color:#1F497D;margin-top:3px">G4, Bank Street Building, Burjuman, Dubai, UAE</div>
		</td>
	</tr>
</table>"""

FOOTER = """<div style="border-top:1px solid #1f2a44;padding-top:5px;text-align:center;font-size:10px;color:#1F497D;font-family:Arial, sans-serif">
	P.O. BOX: 27969. &nbsp;&nbsp; TEL: +971 4 3596160, 3558595 &nbsp;&nbsp; FAX: +971 4 3596181 &nbsp;&nbsp; email: redline@eim.ae
</div>"""


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
