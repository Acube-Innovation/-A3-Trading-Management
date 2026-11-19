"""19 Nov 2026: BOM rows keep the rate to 9 decimals, so per-cm / per-cm² materials
are no longer costed at 0, and materials never bought on a bill are costed at
valuation. Re-cost every BOM on the average price list.

Fixtures sync after patches, so the precision Property Setters are made here too."""

import frappe
from frappe.custom.doctype.property_setter.property_setter import make_property_setter


def execute():
	for field in ("rate", "base_rate"):
		make_property_setter("BOM Item", field, "precision", "9", "Select", validate_fields_for_doctype=False)
		frappe.db.set_value("Property Setter", f"BOM Item-{field}-precision", "module", "A3 Trading Management")
	frappe.clear_cache(doctype="BOM Item")

	from a3_trading_management.api.avg_rate import rebuild_all

	rebuild_all()
