"""Runtime side of the Supervisor role (setup.supervisor builds the permissions).

The workshop portal and its APIs were built for full access: the home screen shows
revenue, Live Stock and Manufacturing show values, and most API methods do not
check roles. A user whose only role is Supervisor gets their own part of it:

- only the pages in PORTAL_PAGES; any other portal page sends them home;
- only the API methods in PORTAL_API_ALLOWED; any other one is refused;
- every answer from an allowed method has its price keys taken out (scrub), and
  the pages that show money build a price-free version for them at the source.

The Desk hides prices with field levels. The Owner holds the Supervisor role too
(the Super Admin profile carries every role) and is not affected.
"""

import json
import re

import frappe
from frappe import _

from a3_trading_management.setup.supervisor import is_supervisor_only

PORTAL_API = "a3_trading_management.api."
PORTAL_HOME = "/a3-workshop"
HR_ROLES = ("HR Manager", "HR User", "System Manager")

PORTAL_PAGES = {
	"", "manufacturing", "quality", "live-stock", "purchase-requisitions", "serial-register",
	"chassis-plate-print", "stock-moves", "quotations", "overtime", "staff-requests", "workshop-print", "boms", "masters", "transactions",
}

# module -> methods, or "*" for every method of the module.
PORTAL_API_ALLOWED = {
	"dashboard": "*",
	"error_log": "*",
	"supervisor_portal": "*",
	"serial_register": "*",
	"stock_board": "*",
	"_common": {"search_items", "search_party", "list_warehouses", "get_document"},
	"buying": {"list_requisitions", "create_requisition", "approve_requisition"},
	"live_stock": {"get_stock_board", "create_material_request"},
	# cancel_inspection is a reversal: the Supervisor never cancels.
	"quality": {"get_board", "get_template_parameters", "get_inspection_form", "record_inspection",
	            "get_inspection", "search_parameters", "add_parameter", "search_items", "save_template",
	            "set_default_template"},
	# Not create_item / create_workstation / create_operation / create_labour: masters
	# the Supervisor cannot create on the Desk either.
	"manufacturing": {"list_labour", "search_labour", "get_work_order_options", "search_items",
	                  "search_raw_items", "get_item_boms", "get_bom_detail", "get_master_options",
	                  "search_operations", "search_workstations", "create_bom", "check_material",
	                  "request_shortfall", "open_sales_orders_for_item", "create_work_order",
	                  "list_work_orders", "get_dashboard", "get_work_order", "update_work_order_tables",
	                  "advance_stage"},
}

# A key is a price when it ends in one of these words (hour_rate, rate_fmt, total_cost,
# grand_total, wip_value...). Quality's pass_rate is a percentage, not a price.
PRICE_KEY = re.compile(
	r"(^|_)(rate|rates|amount|amt|cost|costs|costing|price|valuation|revenue|outstanding|owed|"
	r"billed|paid|wip|money|payments_due|grand_total|net_total|stock_value|value_raw|per_unit)(_fmt|_raw)?$"
)
NOT_PRICE = {"pass_rate", "conversion_rate", "plc_conversion_rate"}
# Second net: text already formatted as money ("د.إ 50,990.00", "AED 1,200.00"),
# whatever its key is called.
MONEY_TEXT = re.compile(r"د\.إ|\bAED\s?-?[\d,]+(\.\d+)?\b")


def scrub(obj):
	"""The same data with every price key, and every money-formatted text, removed."""
	if isinstance(obj, dict):
		return {k: scrub(v) for k, v in obj.items()
		        if k in NOT_PRICE or not (PRICE_KEY.search(str(k)) or (isinstance(v, str) and MONEY_TEXT.search(v)))}
	if isinstance(obj, (list, tuple)):
		return [scrub(v) for v in obj]
	return obj


def _method():
	request = getattr(frappe.local, "request", None)
	if not request:
		return None
	if request.path.startswith("/api/method/"):
		return request.path[len("/api/method/"):]
	return frappe.form_dict.get("cmd")


def _allowed(method):
	module, _, name = method[len(PORTAL_API):].partition(".")
	allowed = PORTAL_API_ALLOWED.get(module)
	return allowed == "*" or (allowed and name in allowed)


def block_portal_api():
	"""auth_hooks: runs on every request once the user is known, API keys included."""
	method = _method()
	if not (method and method.startswith(PORTAL_API) and is_supervisor_only()):
		return
	if not _allowed(method):
		raise frappe.PermissionError(_("This is not open to Supervisors."))
	if method == PORTAL_API + "_common.get_document":
		doctype, name = frappe.form_dict.get("doctype"), frappe.form_dict.get("name")
		if not (doctype and frappe.has_permission(doctype, "read", name)):
			raise frappe.PermissionError(_("This is not open to Supervisors."))


def scrub_portal_response(response=None, request=None):
	"""after_request: take the prices out of a Supervisor's portal API answers."""
	method = _method()
	if not (response and method and method.startswith(PORTAL_API) and is_supervisor_only()):
		return
	if "json" not in (response.content_type or ""):
		return
	try:
		data = json.loads(response.get_data())
	except ValueError:
		return
	if "message" in data:
		data["message"] = scrub(data["message"])
		response.set_data(json.dumps(data, default=str))


def portal_context(context):
	"""Called from website_utils.require_login on every portal page: marks the page
	for a Supervisor and keeps them to their own pages."""
	if not is_supervisor_only():
		return
	page = (frappe.request.path if frappe.request else PORTAL_HOME).rstrip("/")
	page = page[len(PORTAL_HOME):].lstrip("/") if page.startswith(PORTAL_HOME) else ""
	if page not in PORTAL_PAGES:
		frappe.local.flags.redirect_location = PORTAL_HOME
		raise frappe.Redirect
	if context is not None:
		context.supervisor = True


@frappe.whitelist()
def item_dashboard_data(item_code=None, warehouse=None, item_group=None, start=0, sort_by="actual_qty",
                        sort_order="desc"):
	"""The Item form's stock levels carry each bin's valuation rate for the Move / Add
	buttons; a Supervisor gets the quantities without it."""
	from erpnext.stock.dashboard.item_dashboard import get_data

	rows = get_data(item_code, warehouse, item_group, start, sort_by, sort_order)
	if is_supervisor_only():
		for row in rows:
			row.pop("valuation_rate", None)
	return rows


def employee_before_validate(doc, method=None):
	"""Linking a user to an Employee normally adds a User Permission that limits every
	Employee field the user fills in to their own record. A Supervisor enters overtime
	for the whole floor, so theirs is not added (and an existing one is removed)."""
	if doc.user_id and doc.create_user_permission and is_supervisor_only(doc.user_id):
		doc.create_user_permission = 0


def job_requisition_before_validate(doc, method=None):
	"""The Supervisor raising a staff request is the one asking for it."""
	if doc.requested_by:
		return
	employee = frappe.db.get_value("Employee", {"user_id": frappe.session.user, "status": "Active"},
	                               ["name", "company"], as_dict=True)
	if not employee:
		frappe.throw(_("Your user is not linked to an active Employee record, so the request cannot "
		               "say who raised it. Ask HR to set your user on your Employee record."))
	doc.requested_by = employee.name
	doc.company = doc.company or employee.company


def job_requisition_validate(doc, method=None):
	"""Only HR moves a request out of Pending, or changes it once it has left Pending;
	the pay is set when it is approved."""
	if not doc.requested_by:
		frappe.throw(_("Requested By is required."))
	if not set(frappe.get_roles()) & set(HR_ROLES):
		moved = doc.status != "Pending" if doc.is_new() else doc.has_value_changed("status")
		if moved:
			frappe.throw(_("Only HR can approve, reject or close a staff request."))
		before = doc.get_doc_before_save()
		if before and before.status != "Pending":
			frappe.throw(_("This request is {0}; only HR can change it now.").format(_(before.status)))
	if doc.status == "Open & Approved" and not doc.expected_compensation:
		frappe.throw(_("Set the Expected Compensation before approving the request."))
