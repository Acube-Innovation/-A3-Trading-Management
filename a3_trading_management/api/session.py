"""boot_session hook: attaches the app's module list and the user's company to the
Desk boot payload. Runs on every page load, so it stays cheap and never raises."""

import frappe

TRADING_MODULES = ["A3 Trading Management", "Platform"]


def boot_session(bootinfo):
	try:
		company = frappe.defaults.get_user_default("company") or frappe.db.get_default("company")
	except Exception:
		company = None
	bootinfo.a3_trading_management = {"modules": TRADING_MODULES, "company": company}
