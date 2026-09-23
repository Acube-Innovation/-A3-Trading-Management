import frappe

# Roles required by A3 Trading doctype permissions. Created here (and shipped as a
# Role fixture) so the per-task doctypes' DocPerm rows resolve to real Role records
# after install. Doctype sync itself uses ignore_links, so perms import cleanly even
# before these exist; this backfills them idempotently on install and on migrate
# (via patches/v1_0/initial_setup). Reused ERPNext/Frappe HR roles (Accounts User,
# Stock Manager, Employee, Customer, ...) are NOT recreated here.
TRADING_ROLES = [
    "A3 Trading Admin",
    "Workshop Manager",
]

# Portal-only roles get no desk access.
PORTAL_ROLES = set()


def after_install():
    """Bootstrap A3 Trading Management on a fresh site."""
    ensure_roles()
    set_default_settings()
    frappe.db.commit()
    print("A3 Trading Management installed.")



def ensure_roles():
    """Create every custom A3 Trading role if missing (idempotent)."""
    for role_name in TRADING_ROLES:
        if not frappe.db.exists("Role", role_name):
            frappe.get_doc(
                {
                    "doctype": "Role",
                    "role_name": role_name,
                    "desk_access": 0 if role_name in PORTAL_ROLES else 1,
                }
            ).insert(ignore_permissions=True)
    frappe.db.commit()


def set_default_settings():
    """Seed sensible defaults on Single settings doctypes (best-effort, idempotent).

    Each Single is created on first access; we only touch it if the doctype exists
    on this site, so a partial ERPNext/HR install never aborts bootstrap.
    """
    for doctype in _settings_singles():
        try:
            if not frappe.db.exists("DocType", doctype):
                continue
            doc = frappe.get_single(doctype)
            # Touch-save so the Single row materializes with its field defaults.
            doc.flags.ignore_permissions = True
            doc.save(ignore_permissions=True)
        except Exception:
            # A missing dependency on one Single must not block the rest.
            frappe.log_error(
                title=f"A3 Trading after_install: could not init {doctype}",
                message=frappe.get_traceback(),
            )


def _settings_singles():
    """Discover this app's Single settings doctypes from the DocType table."""
    try:
        return [
            d.name
            for d in frappe.get_all(
                "DocType",
                filters={"issingle": 1, "module": ["in", _trading_modules()]},
                fields=["name"],
            )
        ]
    except Exception:
        return []


def _trading_modules():
    from a3_trading_management.api.session import TRADING_MODULES
    return TRADING_MODULES
