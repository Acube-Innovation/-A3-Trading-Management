"""Runs before a3_trading_management's doctypes are synced and its Singles are initialised.

Some a3_trading_management doctypes default a Link to a record that ERPNext only seeds via the
Setup Wizard (e.g. Bulk Fluid Settings.default_uom = "Litre"). On a fresh site the
Setup Wizard may not have run yet, so `init_singles()` fails link validation during
install. Seed those records here so install always succeeds.
"""

import frappe

# UOMs referenced as defaults in a3_trading_management doctypes (Bulk Fluid Settings, Fluid
# Dispense Log). ERPNext ships "Litre" only via the Setup Wizard, so ensure it exists.
_REQUIRED_UOMS = ("Litre",)


def before_install():
    for uom in _REQUIRED_UOMS:
        if not frappe.db.exists("UOM", uom):
            frappe.get_doc({"doctype": "UOM", "uom_name": uom, "must_be_whole_number": 0}).insert(
                ignore_permissions=True
            )
    frappe.db.commit()
