"""Client error intake — the endpoint the workshop portal's global handler posts to.

One whitelisted method, :func:`log_client_error`. It is called from a page that is
already broken, so every decision here favours "record something, never make it
worse":

  * it never raises back at the caller — a reporter that throws inside an error
    handler turns a broken control into a broken page;
  * it collapses repeats onto one row, so a render loop that fires continuously
    costs one record rather than filling the table;
  * it rate-limits per user, so the same loop cannot outrun the collapse on a site
    where each iteration reports a slightly different message;
  * it truncates every string to what the column holds.

Writes go in with ``ignore_permissions``: any logged-in workshop user must be able
to report a fault on the screen in front of them, but none of them should hold
create permission on the log itself (see the doctype's own permissions, which are
Manager-and-up for reading and triage).
"""

import frappe
from frappe.utils import cint, now_datetime, nowdate, nowtime

from a3_trading_management.platform.doctype.garage_error_log.garage_error_log import (
	build_fingerprint,
	clamp,
)

#: Reports accepted from one user per minute. Above this the intake goes quiet:
#: whatever is wrong is already on file, and the point of the log is to be
#: readable. Generous enough that a page failing in a handful of places still
#: records all of them.
RATE_LIMIT_PER_MINUTE = 30

#: How far back to look for the same fault before filing a new row. A week keeps a
#: recurring bug on one line across a working week; older than that and a fresh row
#: is more useful, because it shows the fault came back.
DEDUPE_WINDOW_DAYS = 7

#: Severities the client may set. Anything else is recorded as an Error — an
#: unexpected value is not a reason to drop the report.
SEVERITIES = ("Error", "Warning", "Info")


def _route_of(url):
	"""'/a3-workshop/vehicles' out of a full URL, so errors group by screen."""
	if not url:
		return None
	path = str(url).split("#", 1)[0].split("?", 1)[0]
	for marker in ("://",):
		if marker in path:
			path = "/" + path.split(marker, 1)[1].split("/", 1)[-1]
	return path or None


@frappe.whitelist()
def log_client_error(
	error=None,
	error_type=None,
	stack_trace=None,
	page_url=None,
	page_route=None,
	api_method=None,
	http_status=None,
	source_file=None,
	line_no=None,
	column_no=None,
	reference_doctype=None,
	reference_name=None,
	severity=None,
	viewport=None,
	user_agent=None,
	os_platform=None,
):
	"""Record one browser-side failure. Returns ``{"name", "occurrences"}``.

	Never raises: the caller is a page that has already failed, and a rejected
	report must not become a second error on top of the first.
	"""
	try:
		if frappe.session.user == "Guest":
			# The portal is behind login, so a Guest report is either a stale tab or
			# somebody poking the endpoint. Neither is worth a row.
			return {"ignored": "guest"}

		error = clamp(error, "error")
		if not error:
			return {"ignored": "empty"}

		if _over_rate_limit():
			return {"ignored": "rate-limited"}

		page_route = clamp(page_route or _route_of(page_url), "page_route")
		source_file = clamp(source_file, "source_file")
		error_type = clamp(error_type, "error_type") or "Error"
		line_no = cint(line_no)

		# Same fault already on file? Count it there instead of filing again.
		fingerprint = build_fingerprint(error_type, error, page_route, source_file, line_no)
		existing = _open_log_for(fingerprint)
		if existing:
			doc = frappe.get_doc("Garage Error Log", existing)
			doc.register_repeat()
			return {"name": doc.name, "occurrences": doc.occurrences, "repeat": True}

		# A Dynamic Link to a doctype this site does not have would break the desk
		# form, and an unrecognised name is not worth losing the report over.
		if reference_doctype and not frappe.db.exists("DocType", reference_doctype):
			reference_doctype = reference_name = None

		user_agent = clamp(user_agent, "user_agent")
		doc = frappe.get_doc({
			"doctype": "Garage Error Log",
			"posting_date": nowdate(),
			"posting_time": nowtime(),
			"status": "Open",
			"severity": severity if severity in SEVERITIES else "Error",
			"error_type": error_type,
			"error": error,
			"stack_trace": clamp(stack_trace, "stack_trace"),
			"page_route": page_route,
			"page_url": clamp(page_url, "page_url"),
			"api_method": clamp(api_method, "api_method"),
			"http_status": cint(http_status),
			"source_file": source_file,
			"line_no": line_no,
			"column_no": cint(column_no),
			"reference_doctype": reference_doctype,
			"reference_name": reference_name,
			"user": frappe.session.user,
			"viewport": clamp(viewport, "viewport"),
			"user_agent": user_agent,
			# `browser` is derived from user_agent by the document itself.
			"os_platform": clamp(os_platform, "os_platform"),
		})
		doc.insert(ignore_permissions=True)
		return {"name": doc.name, "occurrences": 1, "repeat": False}
	except Exception:
		# Last line of defence. The report is lost, but the page that was already
		# failing does not get a second failure on top of it.
		frappe.log_error(title="a3_trading_management: client error intake")
		return {"ignored": "intake-failed"}


@frappe.whitelist()
def intake_status():
	"""Is client-error capture actually working on THIS site? Read-only.

	Exists because every failure in the intake path is deliberately silent — a
	reporter that shouts would turn a broken control into a broken page — which
	made "nothing is being logged" impossible to tell apart from "nothing has gone
	wrong". This answers the question directly, from the deployed site itself,
	without needing desk access.

	The commonest cause by far is code deployed without ``bench migrate``, so the
	doctype the intake writes to does not exist yet.
	"""
	installed = bool(frappe.db.exists("DocType", "Garage Error Log"))
	out = {
		"doctype_installed": installed,
		"table_exists": bool(installed and frappe.db.table_exists("Garage Error Log")),
		"user": frappe.session.user,
		"is_guest": frappe.session.user == "Guest",
		"can_read": bool(installed and frappe.has_permission("Garage Error Log", "read")),
		"rate_limit_per_minute": RATE_LIMIT_PER_MINUTE,
		"total_rows": 0,
		"mine_last_hour": 0,
		"recent": [],
	}
	if not out["table_exists"]:
		out["hint"] = (
			"Garage Error Log is not installed on this site. Deploying the code is "
			"not enough — run: bench --site <site> migrate"
		)
		return out

	out["total_rows"] = frappe.db.count("Garage Error Log")
	out["mine_last_hour"] = frappe.db.count(
		"Garage Error Log",
		{
			"user": frappe.session.user,
			"creation": [">", frappe.utils.add_to_date(now_datetime(), hours=-1)],
		},
	)
	# Read with get_all (not get_list): this is the user's own diagnostic and the
	# roles that hit errors in the portal are not the roles that can read the log.
	out["recent"] = frappe.get_all(
		"Garage Error Log",
		filters={"user": frappe.session.user},
		fields=["name", "creation", "error_type", "error", "page_route", "occurrences"],
		order_by="creation desc",
		limit_page_length=5,
	)
	if not out["total_rows"]:
		out["hint"] = "Installed, but nothing has been recorded yet."
	return out


def _over_rate_limit():
	return (
		frappe.db.count(
			"Garage Error Log",
			{
				"user": frappe.session.user,
				"creation": [">", frappe.utils.add_to_date(now_datetime(), minutes=-1)],
			},
		)
		>= RATE_LIMIT_PER_MINUTE
	)


def _open_log_for(fingerprint):
	"""The most recent still-open row for this fault inside the dedupe window.

	Resolved and Ignored rows are deliberately skipped: a fault reappearing after
	somebody closed it is news, and it should come back as a new row rather than
	quietly bumping a counter on a line nobody is looking at any more.
	"""
	return frappe.db.get_value(
		"Garage Error Log",
		{
			"fingerprint": fingerprint,
			"status": ["in", ["Open", "Investigating"]],
			"creation": [">", frappe.utils.add_days(now_datetime(), -DEDUPE_WINDOW_DAYS)],
		},
		"name",
		order_by="creation desc",
	)
