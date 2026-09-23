import frappe
from frappe.sessions import get_csrf_token


def require_login(context=None):
	"""Redirect Guests to the login page, preserving the originally requested path.

	Used by the Workshop website pages under ``www/a3-workshop`` so every page is gated
	behind login. Call from a page's ``get_context``.
	"""
	if frappe.session.user == "Guest":
		redirect_to = frappe.request.path if frappe.request else "/a3-workshop"
		frappe.local.flags.redirect_location = f"/login?redirect-to={redirect_to}"
		raise frappe.Redirect

	if context is not None:
		context.csrf_token = _csrf_token()
	return context


def _csrf_token():
	"""A CSRF token that is guaranteed to be valid for this session.

	``frappe.session.csrf_token`` — the usual template idiom — renders the literal
	"None" when the session has not generated one yet, which is the normal state
	right after login. The POST still succeeds at that point (Frappe skips
	validation when the session holds no token at all), but as soon as anything
	else on that session generates one, the "None" baked into the already-rendered
	page no longer matches and every write from it fails with "Invalid Request".

	``get_csrf_token()`` generates and persists one when the session lacks it, so
	what is rendered always matches what validation compares against. It works
	through ``frappe.local.session_obj``, which only exists inside a real HTTP
	request — outside one, fall back rather than letting a token lookup 500 the
	page.
	"""
	try:
		return get_csrf_token()
	except Exception:
		session = getattr(frappe.local, "session", None)
		return (getattr(session, "data", None) or {}).get("csrf_token") or ""


# --------------------------------------------------------------- signatures

# A person who signs a printed declaration can keep a signature on their own
# record instead of signing every copy by hand. Driver and Employee carry the
# same pair of A3 Trading custom fields for it, so one resolver serves both.
SIGNATURE_FIELDS = ("custom_signature", "custom_signature_image")


def signature_of(doctype, name):
	"""The signature held on one Driver or Employee record, or "".

	The drawn one wins: it is the signature the person gave in person. The
	uploaded image is the fallback for someone who cannot sign on a screen.

	The fields arrive with the A3 Trading fixtures, so a site that has not
	migrated since they shipped gets "" and prints a blank rule rather than
	erroring.
	"""
	if not name:
		return ""
	meta = frappe.get_meta(doctype)
	fields = [f for f in SIGNATURE_FIELDS if meta.has_field(f)]
	if not fields:
		return ""
	row = frappe.db.get_value(doctype, name, fields, as_dict=True)
	if not row:
		return ""
	return row.get("custom_signature") or row.get("custom_signature_image") or ""


def signable(doctype, name):
	"""Where a signature drawn on a print page is filed, or None.

	A rule whose name belongs to a real Driver or Employee can be signed on the
	screen and the signature kept on that person's own record, so the next print
	offers it as the signature on file. A rule for somebody the system holds no
	record of — a customer at the counter, the tool crib, a witness — can still be
	signed, but only for the page being printed.

	Paired with :func:`signature_of`: that reads the signature back, this says
	where a new one goes. Both are handed to the chooser in templates/print_base.html.
	"""
	if not name or doctype not in ("Driver", "Employee"):
		return None
	if not frappe.db.exists(doctype, name):
		return None
	return {"doctype": doctype, "name": name}


def employee_of(user):
	"""The Employee record behind a user, or "".

	The inspector on a handover and the advisor on a job card are recorded as
	users; their signature lives on the Employee row that names them.
	"""
	if not user or user == "Guest":
		return ""
	return frappe.db.get_value("Employee", {"user_id": user, "status": "Active"}, "name") or (
		frappe.db.get_value("Employee", {"user_id": user}, "name") or ""
	)
