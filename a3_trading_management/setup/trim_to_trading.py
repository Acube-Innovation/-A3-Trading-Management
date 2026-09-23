# Implements: the database half of trimming the app to trading and manufacturing --
# run ONCE, with the module folders still on disk, then remove the folders.
"""Delete the workshop-service modules from the site.

    bench --site <site> execute a3_trading_management.setup.trim_to_trading.run

Job cards, appointments, estimates, telecalling, service reminders, inspection and
repair warranty are not part of A3 Trading. This removes their record types (and
tables), reports, workspaces, notifications, workflows and the custom fields that
only served them, using the same primitives `bench uninstall-app` uses. Run it while
the doctype folders still exist: in developer mode Frappe deletes each doctype's
folder as it deletes the record. Idempotent -- a second run finds nothing to do.
"""

import json
import os

import frappe
from frappe.installer import _delete_doctypes, _delete_modules

APP = "a3_trading_management"
MODULE = "A3 Trading Management"
REMOVE_MODULES = [
	"Workshop", "Scheduling", "Estimating", "Communications", "Trading CRM",
	"Unit Inspection", "Warranty and Claims",
]


def run():
	# The work-order stage history stays: it is manufacturing, not workshop.
	if frappe.db.exists("DocType", "Workshop Work Order Stage Log"):
		frappe.db.set_value("DocType", "Workshop Work Order Stage Log", "module", MODULE, update_modified=False)

	modules = [m for m in REMOVE_MODULES if frappe.db.exists("Module Def", m)]
	going = frappe.get_all("DocType", filters={"module": ["in", modules]}, pluck="name") if modules else []

	# Workflows and custom permissions have no module and would be orphaned.
	for wf in frappe.get_all("Workflow", filters={"document_type": ["in", going or [""]]}, pluck="name"):
		frappe.delete_doc("Workflow", wf, ignore_permissions=True, force=True)
	for perm in frappe.get_all("Custom DocPerm", filters={"parent": ["in", going or [""]]}, pluck="name"):
		frappe.delete_doc("Custom DocPerm", perm, ignore_permissions=True, force=True)

	if modules:
		drop = _delete_modules(modules, dry_run=False)
		_delete_doctypes(drop, dry_run=False)

	# Custom fields, property setters, client scripts and print formats that came
	# with the fork but are not in the trimmed fixtures any more.
	fx = os.path.join(frappe.get_app_path(APP), "fixtures")
	def rows(name):
		p = os.path.join(fx, name)
		return json.load(open(p)) if os.path.exists(p) else []
	keep_cf = {(r["dt"], r["fieldname"]) for r in rows("custom_field.json")}
	keep_ps = {(r["doc_type"], r.get("field_name") or "", r["property"]) for r in rows("property_setter.json")}
	keep_cs = {r["name"] for r in rows("client_script.json")}
	keep_pf = {r["name"] for r in rows("print_format.json")}
	keep_roles = {r["role_name"] for r in rows("role.json")}
	removed = {"Custom Field": 0, "Property Setter": 0, "Client Script": 0, "Print Format": 0, "Role": 0}
	for r in frappe.get_all("Custom Field", filters={"module": MODULE}, fields=["name", "dt", "fieldname"]):
		if (r.dt, r.fieldname) not in keep_cf:
			frappe.delete_doc("Custom Field", r.name, ignore_permissions=True, force=True); removed["Custom Field"] += 1
	for r in frappe.get_all("Property Setter", filters={"module": MODULE}, fields=["name", "doc_type", "field_name", "property"]):
		if (r.doc_type, r.field_name or "", r.property) not in keep_ps:
			frappe.delete_doc("Property Setter", r.name, ignore_permissions=True, force=True); removed["Property Setter"] += 1
	for dt, keep, key in (("Client Script", keep_cs, "Client Script"), ("Print Format", keep_pf, "Print Format")):
		for name in frappe.get_all(dt, filters={"module": MODULE}, pluck="name"):
			if name not in keep:
				frappe.delete_doc(dt, name, ignore_permissions=True, force=True); removed[key] += 1
	# Roles the fork created that nothing references now and nobody holds.
	for role in frappe.get_all("Role", filters={"is_custom": 1}, pluck="name"):
		if role in keep_roles or frappe.db.count("Has Role", {"role": role}):
			continue
		if frappe.db.count("DocPerm", {"role": role}) or frappe.db.count("Custom DocPerm", {"role": role}):
			continue
		frappe.delete_doc("Role", role, ignore_permissions=True, force=True); removed["Role"] += 1

	frappe.clear_cache()
	frappe.db.commit()
	print("modules removed:", modules)
	print("doctypes removed:", len(going))
	for k, v in removed.items():
		print(f"{k:16s} removed: {v}")
