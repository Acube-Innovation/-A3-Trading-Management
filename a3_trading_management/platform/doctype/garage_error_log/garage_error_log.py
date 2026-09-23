"""Garage Error Log — what the browser broke on, kept so it can be sorted and fixed.

The workshop portal is a set of server-rendered pages driven by inline JavaScript.
When one of those scripts throws, the page half-renders and the user sees a control
that silently does nothing — the stack goes to a browser console nobody has open, on
a tablet in a workshop bay. Nothing reaches the server, so there is no way to know a
screen is broken until somebody complains about it in person.

This doctype is where those go. ``a3_trading_management.api.error_log.log_client_error`` is the
whitelisted endpoint the portal's global handler posts to; every field here exists to
answer one triage question:

  * posting_date / posting_time — when
  * error_type / error / stack_trace — what
  * page_route / page_url / source_file / line_no — where in the app
  * api_method / http_status — which server call, if it was one
  * reference_doctype / reference_name — which record was on screen
  * user / browser / os_platform / viewport / user_agent — who, on what
  * fingerprint / occurrences — how often, and is this the same bug as that one

Repeats collapse: identical errors increment ``occurrences`` on the open row rather
than filing a new one, so a loop that fires two thousand times is one line to triage
and not two thousand.
"""

import hashlib
import re

import frappe
from frappe.model.document import Document
from frappe.utils import now_datetime, nowdate, nowtime

#: Column widths this doctype writes into. Anything longer is truncated on the way
#: in — a browser can hand over a megabyte of minified stack, and losing the tail of
#: one stack is better than losing the whole record to a database error.
MAX = {
	"error": 1000,
	"error_type": 140,
	"stack_trace": 10000,
	"page_route": 255,
	"page_url": 1000,
	"api_method": 255,
	"source_file": 255,
	"user_agent": 500,
	"browser": 80,
	"os_platform": 80,
	"viewport": 40,
	"resolution_notes": 1000,
}


def clamp(value, field):
	"""Trim `value` to what `field` can hold, marking anything cut."""
	if value is None:
		return None
	text = str(value).strip()
	limit = MAX.get(field)
	if limit and len(text) > limit:
		return text[: limit - 1] + "…"
	return text


#: Runs of digits are replaced before hashing, so the same fault carrying a
#: different record id, timestamp or count on each occurrence stays one fault:
#: "… while loading VEH-0001" and "… while loading VEH-0002" both normalise to
#: "… while loading VEH-#". Truncating alone did not do this — an id inside a
#: short message sits well within the first 200 characters and split it.
#: Positional information is NOT lost to this: line_no and source_file are
#: separate components of the hash.
_DIGIT_RUN = re.compile(r"\d+")


def build_fingerprint(error_type, error, page_route, source_file, line_no):
	"""A stable id for "the same bug", used to collapse repeats.

	Built from the message's first 200 characters with digit runs normalised away,
	rather than the raw text: the same fault routinely carries a varying id or
	count, and hashing that verbatim would file every occurrence separately —
	which is the flood this exists to prevent.
	"""
	message = _DIGIT_RUN.sub("#", (error or "")[:200])
	basis = "|".join(
		str(p or "") for p in (error_type, message, page_route, source_file, line_no)
	)
	return hashlib.sha1(basis.encode("utf-8")).hexdigest()


def browser_from_user_agent(user_agent):
	"""A coarse browser name. Enough to spot "only on Safari", not a UA parser.

	Derived on the document rather than at the API, so a row written by a seeder,
	a patch or the desk carries it too — not only one filed through
	:func:`a3_trading_management.api.error_log.log_client_error`.
	"""
	ua = user_agent or ""
	# Order matters: all of these also claim to be Mozilla, and Chrome's UA
	# additionally claims Safari.
	for token, label in (
		("Edg/", "Edge"), ("OPR/", "Opera"), ("Firefox/", "Firefox"),
		("Chrome/", "Chrome"), ("Safari/", "Safari"),
	):
		if token in ua:
			return label
	return None


class GarageErrorLog(Document):
	def __init__(self, *args, **kwargs):
		super().__init__(*args, **kwargs)
		# Cleared here, not in validate(): Document.insert() calls _validate_links()
		# BEFORE both before_insert and validate, so a reference_name with no
		# reference_doctype is rejected as "Doctype must be set first" before either
		# of those could tidy it up. A report must never be lost to a stray link.
		if not self.get("reference_doctype"):
			self.reference_name = None

	def before_insert(self):
		# A client can post without them; the server's clock is the one to trust for
		# ordering anyway, so it fills the blanks rather than rejecting the report.
		self.posting_date = self.posting_date or nowdate()
		self.posting_time = self.posting_time or nowtime()
		self.user = self.user or frappe.session.user
		self.first_seen = self.first_seen or now_datetime()
		self.last_seen = self.last_seen or self.first_seen
		self.occurrences = self.occurrences or 1

	def validate(self):
		for field in MAX:
			if self.get(field):
				self.set(field, clamp(self.get(field), field))
		# Recomputed on every save so an edited message cannot leave the row
		# fingerprinted against text it no longer holds.
		self.fingerprint = build_fingerprint(
			self.error_type, self.error, self.page_route, self.source_file, self.line_no
		)
		# Again on every save, so clearing the doctype on an existing row takes the
		# now-meaningless name with it (__init__ only covers construction).
		if not self.reference_doctype:
			self.reference_name = None
		# Filled from the agent string unless somebody set it explicitly.
		if self.user_agent and not self.browser:
			self.browser = browser_from_user_agent(self.user_agent)

	def register_repeat(self, when=None):
		"""Count one more sighting of this same error.

		Written with db_set rather than save(): a repeat is not an edit anyone needs
		in the version history, and this path runs on the hot side of a page that is
		already failing.
		"""
		self.db_set("occurrences", (self.occurrences or 1) + 1, update_modified=False)
		self.db_set("last_seen", when or now_datetime(), update_modified=False)
