import frappe
from frappe.tests.utils import FrappeTestCase

from a3_trading_management.api.error_log import log_client_error
from a3_trading_management.platform.doctype.garage_error_log.garage_error_log import (
	MAX,
	build_fingerprint,
)

# The runner walks Link fields and auto-builds a test record for each target; the
# reference_doctype link points at DocType itself, which must not be fixtured.
test_ignore = ["DocType", "User"]

UA_SAFARI = (
	"Mozilla/5.0 (iPad; CPU OS 17_5 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) "
	"Version/17.5 Mobile/15E148 Safari/604.1"
)


class TestGarageErrorLog(FrappeTestCase):
	def test_defaults_are_filled_server_side(self):
		"""A client may post none of these; the server's clock is authoritative."""
		doc = frappe.get_doc({
			"doctype": "Garage Error Log",
			"error": "ZZ boom",
		}).insert(ignore_permissions=True)
		self.assertTrue(doc.posting_date)
		self.assertTrue(doc.posting_time)
		self.assertEqual(doc.status, "Open")
		self.assertEqual(doc.severity, "Error")
		self.assertEqual(doc.occurrences, 1)
		self.assertEqual(doc.user, frappe.session.user)
		self.assertTrue(doc.first_seen)
		self.assertTrue(doc.last_seen)

	def test_long_values_are_truncated_to_the_column(self):
		"""A minified stack can be megabytes; losing its tail beats losing the row."""
		doc = frappe.get_doc({
			"doctype": "Garage Error Log",
			"error": "Z" * 5000,
			"stack_trace": "S" * 50000,
			"page_url": "u" * 4000,
		}).insert(ignore_permissions=True)
		self.assertEqual(len(doc.error), MAX["error"])
		self.assertEqual(len(doc.stack_trace), MAX["stack_trace"])
		self.assertEqual(len(doc.page_url), MAX["page_url"])

	def test_fingerprint_ignores_the_varying_tail_of_a_message(self):
		"""The same fault often carries a different record id each time."""
		a = build_fingerprint("TypeError", "x is null while loading VEH-0001", "/v", "f.js", 10)
		b = build_fingerprint("TypeError", "x is null while loading VEH-0002", "/v", "f.js", 10)
		self.assertEqual(a, b, "ids past the first 200 chars must not split the fault")
		c = build_fingerprint("TypeError", "x is null", "/v", "f.js", 11)
		self.assertNotEqual(a, c, "a different line is a different fault")

	def test_dangling_dynamic_link_is_dropped(self):
		doc = frappe.get_doc({
			"doctype": "Garage Error Log",
			"error": "ZZ orphan link",
			"reference_name": "SOME-RECORD",
		}).insert(ignore_permissions=True)
		self.assertIsNone(doc.reference_name)

	def test_repeat_collapses_onto_the_open_row(self):
		first = log_client_error(
			error="ZZ repeated fault", error_type="TypeError",
			page_url="http://x/a3-workshop/vehicles?q=1", source_file="v.js", line_no=7,
		)
		second = log_client_error(
			error="ZZ repeated fault", error_type="TypeError",
			page_url="http://x/a3-workshop/vehicles?q=2", source_file="v.js", line_no=7,
		)
		self.assertEqual(first["name"], second["name"])
		self.assertEqual(second["occurrences"], 2)
		self.assertTrue(second["repeat"])

	def test_a_resolved_fault_coming_back_opens_a_new_row(self):
		"""A fault reappearing after somebody closed it is news, not a counter bump."""
		first = log_client_error(error="ZZ closed then back", error_type="TypeError")
		frappe.db.set_value("Garage Error Log", first["name"], "status", "Resolved")
		again = log_client_error(error="ZZ closed then back", error_type="TypeError")
		self.assertNotEqual(again["name"], first["name"])

	def test_intake_derives_route_and_browser(self):
		res = log_client_error(
			error="ZZ derived fields",
			page_url="http://127.0.0.1:8000/a3-workshop/front-office?customer=ACME#tab",
			user_agent=UA_SAFARI,
		)
		doc = frappe.get_doc("Garage Error Log", res["name"])
		self.assertEqual(doc.page_route, "/a3-workshop/front-office")
		self.assertEqual(doc.browser, "Safari")

	def test_intake_never_raises_on_bad_input(self):
		"""It runs inside a page that has already failed; it must not add a second."""
		self.assertEqual(log_client_error(error=None).get("ignored"), "empty")
		self.assertEqual(log_client_error(error="").get("ignored"), "empty")
		# An unknown doctype must not take the report down with it.
		res = log_client_error(error="ZZ unknown ref", reference_doctype="No Such Doctype")
		self.assertTrue(res.get("name"))
		self.assertIsNone(frappe.db.get_value("Garage Error Log", res["name"], "reference_doctype"))

	def test_unknown_severity_falls_back_to_error(self):
		res = log_client_error(error="ZZ odd severity", severity="Catastrophic")
		self.assertEqual(frappe.db.get_value("Garage Error Log", res["name"], "severity"), "Error")

	def test_guest_reports_are_refused(self):
		"""The portal is behind login; a Guest post is a stale tab or a poke."""
		original = frappe.session.user
		try:
			frappe.set_user("Guest")
			self.assertEqual(log_client_error(error="ZZ guest").get("ignored"), "guest")
		finally:
			frappe.set_user(original)
