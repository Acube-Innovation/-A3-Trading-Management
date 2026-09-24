app_name = "a3_trading_management"
app_title = "A3 Trading Management"
app_publisher = "Acube Innovations Private Limited"
app_description = "Trailer trading on ERPNext: serials and chassis numbering, buying and selling, and the workshop portal."
app_email = "admin@acube.co"
app_license = "mit"
required_apps = ["frappe", "erpnext"]

# This app is self-contained: trading and manufacturing on ERPNext, with its own
# portal (templates, stylesheet, pages). The workshop-service side of the platform
# it grew out of (job cards, appointments, estimates, telecalling, inspection,
# repair warranty) was removed on 23 Sep 2026 -- see setup/trim_to_trading.py.

# Installation
# ------------
before_install = "a3_trading_management.setup.before_install.before_install"
after_install = [
	"a3_trading_management.setup.after_install.after_install",   # roles, settings singles
	"a3_trading_management.setup.install.after_install",         # trading custom fields
]
after_migrate = "a3_trading_management.setup.install.after_migrate"

# Desk
# ----
boot_session = "a3_trading_management.api.session.boot_session"
app_include_js = ["a3_trading_management.bundle.js", "/assets/a3_trading_management/js/workshop_navbar.js"]
app_include_css = "/assets/a3_trading_management/css/workshop_navbar.css"
doctype_js = {"Item": "public/js/item.js"}

# Portal
# ------
home_page = "a3-workshop"
# The vehicle register and the workshop-service screens are not part of this
# product; old links land on the portal home.
website_redirects = [
	{"source": r"/a3-workshop/vehicles", "target": "/a3-workshop"},
	{"source": r"/a3-workshop/vehicle-entry", "target": "/a3-workshop"},
	{"source": r"/a3-workshop/vehicle-history(-detail|-handover|-print)?", "target": "/a3-workshop"},
	{"source": r"/a3-workshop/job-cards", "target": "/a3-workshop"},
	{"source": r"/a3-workshop/job-card(/.*)?", "target": "/a3-workshop"},
	{"source": r"/a3-workshop/front-office", "target": "/a3-workshop/customers"},
	{"source": r"/a3-workshop/daily-planner", "target": "/a3-workshop"},
	{"source": r"/a3-workshop/telecalling", "target": "/a3-workshop"},
	{"source": r"/a3-workshop/complaints", "target": "/a3-workshop"},
	{"source": r"/a3-workshop/technician-board", "target": "/a3-workshop"},
	{"source": r"/a3-workshop/tool-declaration", "target": "/a3-workshop"},
]

# Fixtures
# --------
# Export filter only: import walks the fixtures directory. Modules match modules.txt.
TRADING_MODULES = ["A3 Trading Management", "Platform"]
fixtures = [
	{"dt": "Role", "filters": [["is_custom", "=", 1]]},
	{"dt": "Custom Field", "filters": [["module", "in", TRADING_MODULES]]},
	{"dt": "Property Setter", "filters": [["module", "in", TRADING_MODULES]]},
	{"dt": "Client Script", "filters": [["module", "in", TRADING_MODULES]]},
	{"dt": "Workflow", "filters": [["document_type", "!=", ""]]},
	{"dt": "Dashboard Chart", "filters": [["module", "in", TRADING_MODULES]]},
	{"dt": "Number Card", "filters": [["module", "in", TRADING_MODULES]]},
	{"dt": "Notification", "filters": [["module", "in", TRADING_MODULES]]},
	{"dt": "Print Format", "filters": [["module", "in", TRADING_MODULES]]},
	{"dt": "Module Def", "filters": [["app_name", "=", "a3_trading_management"]]},
]

# Document Events (ERPNext / core doctypes only; this app's own doctypes have controllers)
# ---------------
doc_events = {
	# Items are numbered from a series while Item Code stays a typeable field.
	"Item": {
		"before_naming": "a3_trading_management.integrations.item.before_naming",
	},
	# Phase 2: serials are minted from Work Order events so that a work order raised
	# from the Desk, an import or the portal all behave the same way.
	"Work Order": {
		"after_insert": "a3_trading_management.serial_control.create_serials",
		"on_update": "a3_trading_management.serial_control.sync_stage",
		"on_submit": "a3_trading_management.serial_control.sync_stage",
		# The production stage is allow_on_submit, so advancing it on a submitted
		# work order comes through update_after_submit -- the path that matters.
		"on_update_after_submit": "a3_trading_management.serial_control.sync_stage",
		"on_cancel": "a3_trading_management.serial_control.on_work_order_cancel",
	},
	# Task 14: a purchase order cannot go to a supplier whose papers have lapsed.
	"Purchase Order": {
		"validate": "a3_trading_management.api.buying.block_non_compliant_supplier",
	},
	# Task 9: the Manufacture entry is when the trailer really enters stock.
	"Stock Entry": {
		"before_validate": "a3_trading_management.serial_control.set_stock_entry_type",
		"on_submit": "a3_trading_management.serial_control.on_manufacture_entry",
	},
	# Task 23: a dispatched trailer has left the yard and has a new owner.
	"Delivery Note": {
		"on_submit": "a3_trading_management.api.selling.mark_delivered",
	},
}
