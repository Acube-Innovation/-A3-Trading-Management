"""Signatures held on a person's record (a3_trading_management.api.signature.*).

A printed declaration -- a tool issue, a delivery declaration -- is worth
nothing unless the signature on it belongs to the person named. The print pages
offer three ways to get one onto the rule: the signature already held on the
record, an image uploaded for that printout alone, or signing on the screen then
and there. This module is the last of those: it takes the drawn signature and
files it on the person, so the next print offers it as the signature on file.

The image lands on ``custom_signature`` -- the Signature field this app's
fixtures add to Employee -- which is exactly what
``a3_trading_management.website_utils.signature_of`` reads back.
"""

import base64
import binascii

import frappe
from frappe import _

# Only people sign. A writable Link field on any other doctype is not a place to
# park an image, and an open doctype argument here would be exactly that.
_SIGNABLE = ("Employee",)

# The Signature field stores a data URL. PNG is what a canvas produces and JPEG
# is what a photographed signature arrives as; SVG is excluded on purpose --
# it carries script, and this value is rendered straight back into a page.
_PREFIXES = ("data:image/png;base64,", "data:image/jpeg;base64,", "data:image/jpg;base64,")

# A drawn signature is a few KB. The cap is generous for a photographed one and
# still small enough that the column cannot be used as file storage.
_MAX_BYTES = 1024 * 1024


@frappe.whitelist()
def save_signature(doctype, name, image):
	"""File a drawn signature on one Employee.

	Returns the stored data URL, which is what the page paints onto the rule --
	so what gets printed is what was saved, not the canvas it came from.
	"""
	if frappe.session.user == "Guest":
		frappe.throw(_("You must be logged in."), frappe.PermissionError)

	doctype = (doctype or "").strip()
	name = (name or "").strip()
	if doctype not in _SIGNABLE:
		frappe.throw(_("A signature can only be filed on an Employee."))
	if not name or not frappe.db.exists(doctype, name):
		frappe.throw(_("{0} {1} not found.").format(_(doctype), name), frappe.DoesNotExistError)

	# Writing someone's signature is writing their record: the same permission,
	# checked the same way, rather than a role list that drifts from the doctype's.
	if not frappe.has_permission(doctype, "write", doc=name):
		frappe.throw(
			_("You do not have permission to update {0} {1}.").format(_(doctype), name),
			frappe.PermissionError,
		)

	image = _clean(image)

	if not frappe.get_meta(doctype).has_field("custom_signature"):
		# The field ships as a fixture; a site that has not migrated since it was
		# added would otherwise fail on an unknown column, which says nothing.
		frappe.throw(
			_("This site has no signature field on {0} yet. Run bench migrate and try again.").format(
				_(doctype)
			)
		)

	# db.set_value, not a full save: filing a signature must not re-run Employee's
	# expired licence, a missing approver) would refuse a signature that is only
	# being written to one field.
	frappe.db.set_value(doctype, name, "custom_signature", image, update_modified=True)
	return {"signature": image}


def _clean(image):
	"""Reject anything that is not a small PNG/JPEG data URL."""
	image = (image or "").strip()
	if not image:
		frappe.throw(_("No signature was drawn."))

	prefix = next((p for p in _PREFIXES if image.startswith(p)), None)
	if not prefix:
		frappe.throw(_("A signature must be a PNG or JPEG image."))

	payload = image[len(prefix):]
	try:
		raw = base64.b64decode(payload, validate=True)
	except (binascii.Error, ValueError):
		frappe.throw(_("That signature image could not be read."))

	if not raw:
		frappe.throw(_("No signature was drawn."))
	if len(raw) > _MAX_BYTES:
		frappe.throw(_("That signature image is too large — draw it again, or upload a smaller file."))

	return image
