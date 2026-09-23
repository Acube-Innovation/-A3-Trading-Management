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
# repair warranty) is not part of it -- see setup/trim_to_trading.py.

# Installation
# ------------
before_install = "a3_trading_management.setup.before_install.before_install"
after_install = "a3_trading_management.setup.after_install.after_install"   # roles, settings singles

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
}
