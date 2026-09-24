# Shared helpers for A3 Trading ERPNext/HR integration handlers.
# These fire AFTER ERPNext's own controller logic; every handler is additive and
# meta-guards custom columns (B035/B030) so a vanilla ERPNext bench is untouched.
import frappe


def has_col(doctype, column):
    """Safe wrapper around frappe.db.has_column (B030/B035)."""
    try:
        return bool(frappe.db.has_column(doctype, column))
    except Exception:
        return False


def single_has(doctype, fieldname):
    """True if a Single doctype actually defines fieldname (B029 guard)."""
    try:
        meta = frappe.get_meta(doctype)
        return bool(meta.get_field(fieldname))
    except Exception:
        return False


def select_options(doctype, fieldname):
    """Return the set of allowed values for a Select field, or empty set."""
    try:
        opt = frappe.get_meta(doctype).get_field(fieldname).options or ""
    except Exception:
        return set()
    return {o.strip() for o in opt.split("\n") if o.strip()}


def doctype_exists(doctype):
    try:
        return bool(frappe.db.exists("DocType", doctype))
    except Exception:
        return False


def resolve(dotted_path):
    """Return the callable at dotted_path, or None if it cannot be imported."""
    try:
        return frappe.get_attr(dotted_path)
    except Exception:
        return None


def item_flag(item_code, column):
    """Boolean value of a custom flag on Item, meta-guarded."""
    if not item_code or not has_col("Item", column):
        return False
    return bool(frappe.db.get_value("Item", item_code, column))
