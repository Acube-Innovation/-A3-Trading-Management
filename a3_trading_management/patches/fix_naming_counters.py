"""25 Nov 2026: the cancelled-voucher clean-up left some naming counters behind
the highest existing number (Sales Invoice at 3 with ACC-SINV-2026-00004 on file,
Payment Entry at 15 with ACC-PAY-2026-00016), so the next new document failed
with "Duplicate entry". Each counter is raised to the highest number in use;
a counter is never lowered."""

import frappe

SERIES = {
	"ACC-SINV-2026-": "Sales Invoice",
	"ACC-PAY-2026-": "Payment Entry",
	"ACC-PINV-2026-": "Purchase Invoice",
	"SAL-ORD-2026-": "Sales Order",
	"SAL-QTN-2026-": "Quotation",
	"RLI-TR-QT-": "Quotation",
	"RLI-TR-DN-2026-": "Delivery Note",
	"MFG-WO-2026-": "Work Order",
	"ACC-JV-2026-": "Journal Entry",
	"MAT-STE-2026-": "Stock Entry",
}


def execute():
	for prefix, doctype in SERIES.items():
		if not frappe.db.table_exists(doctype):
			continue
		top = frappe.db.sql(
			f"""select max(cast(substring_index(substring(name, {len(prefix) + 1}), '-', 1) as unsigned))
			from `tab{doctype}` where name like %s""",
			prefix.replace("_", "\\_") + "%",
		)[0][0] or 0
		current = frappe.db.sql("select current from `tabSeries` where name = %s", prefix)
		if current and (current[0][0] or 0) < top:
			frappe.db.sql("update `tabSeries` set current = %s where name = %s", (top, prefix))
