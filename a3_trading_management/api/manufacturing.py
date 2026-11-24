"""Manufacturing facade (a3_trading_management.api.manufacturing.*).

Backs the Workshop Manufacturing page (a3_trading_management
www/a3-trading/manufacturing.html and its mirror in a3_workshop_management).

The page produces stock by manufacturing an Item: pick an Item that has an
active BOM, its default BOM auto-loads, a Work Order is created (kept in Draft),
and it is walked through a custom operational stage flow --

    Material -> Fabrication -> Assembly -> Paint -> QC -> Delivery

-- tracked in the `custom_production_stage` field with a per-transition audit
line in `custom_stage_history` (Workshop Work Order Stage Log). While the Work
Order is in Draft the required-items and operations tables stay editable; on the
final transition into Delivery the Work Order is submitted and a Manufacture
Stock Entry is posted so the finished item is actually added to inventory.

No customer is involved -- this flow exists only to build stock. Everything is
written with ignore_permissions so workshop portal roles (Service Advisor etc.)
can drive it without full Manufacturing desk permissions, mirroring the idiom
used across the a3_trading_management.api.* layer.
"""

import json

import frappe
from frappe import _
from frappe.utils import (
    cint,
    flt,
    fmt_money,
    formatdate,
    get_datetime,
    getdate,
    now_datetime,
    time_diff_in_hours,
)

from erpnext.manufacturing.doctype.work_order.work_order import make_stock_entry

# Custom operational stage flow shown on the portal (distinct from ERPNext's own
# docstatus-driven Work Order status). Order matters -- index == step.
STAGES = ["Material", "Fabrication", "Assembly", "Paint", "QC", "Delivery"]

# Frontend badge class per stage, so the list/detail styling stays server-driven.
_STAGE_BADGE = {
    "Material": "badge--navy",
    "Fabrication": "badge--progress",
    "Assembly": "badge--progress",
    "Paint": "badge--progress",
    "QC": "badge--pending",
    "Delivery": "badge--success",
}


# ---------------------------------------------------------------------------
# Shared helpers (nothing below is whitelisted directly)
# ---------------------------------------------------------------------------


def _current_company():
    """Session user's company, for defaults only (never derived from Branch)."""
    return frappe.defaults.get_user_default("Company") or frappe.db.get_default("company")


def _company_currency(company=None):
    company = company or _current_company()
    return (company and frappe.get_cached_value("Company", company, "default_currency")) or "AED"


def _money(value, currency=None):
    """Format a number the way the page expects (e.g. 'AED 1,710.00')."""
    return fmt_money(flt(value), currency=currency or _company_currency())


def _stage_index(stage):
    try:
        return STAGES.index(stage or "Material")
    except ValueError:
        return 0


def _ensure_editable_setting():
    """ERPNext only preserves hand-edited required_items when this Manufacturing
    Setting is on; otherwise it resets qty from the BOM on every save. We rely on
    per-stage editing, so make sure it is enabled (idempotent, one-time flip)."""
    if not frappe.db.get_single_value(
        "Manufacturing Settings", "allow_editing_of_items_and_quantities_in_work_order"
    ):
        frappe.db.set_single_value(
            "Manufacturing Settings",
            "allow_editing_of_items_and_quantities_in_work_order",
            1,
        )


def _non_transit_warehouses(company):
    """Selectable stock warehouses, Transit excluded (a Manufacture entry can't
    derive valuation from a transit warehouse)."""
    filters = {"is_group": 0, "disabled": 0, "warehouse_type": ["!=", "Transit"]}
    if company:
        filters["company"] = company
    return frappe.get_all("Warehouse", filters=filters, pluck="name", order_by="name")


def _default_warehouses():
    company = _current_company()
    wip = frappe.db.get_single_value("Manufacturing Settings", "default_wip_warehouse")
    fg = frappe.db.get_single_value("Manufacturing Settings", "default_fg_warehouse")

    names = _non_transit_warehouses(company)

    def by_kw(keywords):
        for w in names:
            lw = w.lower()
            if any(k in lw for k in keywords):
                return w
        return None

    if not fg:
        fg = by_kw(["finished"]) or (names[0] if names else None)
    if not wip:
        wip = by_kw(["work in progress", "wip", "stores", "store"]) or fg
    return wip, fg


def _default_bom_for_item(item):
    """Preferred active BOM for an item: the default one, else any active BOM."""
    if not item:
        return None
    bom = frappe.db.get_value("BOM", {"item": item, "is_active": 1, "is_default": 1}, "name")
    if not bom:
        bom = frappe.db.get_value("BOM", {"item": item, "is_active": 1}, "name")
    return bom


def _ensure_operation(name):
    """Return an Operation master name, creating a thin one if it does not exist
    (so the portal can add free-text operations to a Work Order)."""
    name = (name or "").strip()
    if not name:
        return None
    if not frappe.db.exists("Operation", name):
        # Operation is Prompt-named: the docname is the operation label.
        op = frappe.new_doc("Operation")
        op.flags.ignore_permissions = True
        op.insert(ignore_permissions=True, set_name=name)
    return name


def _ensure_workstation(name):
    """Return a Workstation master name, creating a thin one if missing."""
    name = (name or "").strip()
    if not name:
        return None
    if not frappe.db.exists("Workstation", name):
        ws = frappe.new_doc("Workstation")
        ws.workstation_name = name
        ws.flags.ignore_permissions = True
        ws.insert(ignore_permissions=True)
    return name


def _labour(employee):
    """A valid Employee name or None (labour is optional on every operation)."""
    employee = (employee or "").strip()
    if not employee:
        return None
    if not frappe.db.exists("Employee", employee):
        frappe.throw(_("Labour {0} not found").format(employee))
    return employee


def _labour_ids(row):
    """Every worker on an operation row: Labour first, then More Labour."""
    ids = [row.get("custom_labour")] + (row.get("custom_more_labour") or "").split(",")
    out = []
    for e in ids:
        e = (e or "").strip()
        if e and e not in out:
            out.append(e)
    return out


def _labour_fields(labours=None, single=None):
    """A list of workers (or the old single one) as the two row fields."""
    if labours is None:
        labours = [single] if single else []
    elif isinstance(labours, str):
        labours = labours.split(",")
    ids = []
    for e in labours:
        e = _labour(e)
        if e and e not in ids:
            ids.append(e)
    return {"custom_labour": ids[0] if ids else None, "custom_more_labour": ", ".join(ids[1:]) or None}


def _copy_labour_from_bom(wo):
    """ERPNext copies BOM operations field by field and knows nothing of the
    labour column, so carry it across: matched by BOM row order, else by name."""
    if not wo.bom_no:
        return
    bom_ops = frappe.get_all(
        "BOM Operation", filters={"parent": wo.bom_no, "parenttype": "BOM"},
        fields=["idx", "operation", "custom_labour", "custom_more_labour"], order_by="idx",
    )
    by_idx = {r.idx: r for r in bom_ops}
    by_op = {}
    for r in bom_ops:
        by_op.setdefault(r.operation, r)
    for d in wo.operations:
        src = by_idx.get(d.idx)
        if not src or src.operation != d.operation:
            src = by_op.get(d.operation)
        if src and src.custom_labour and not d.get("custom_labour"):
            d.custom_labour = src.custom_labour
            d.custom_more_labour = src.get("custom_more_labour")


# ---------------------------------------------------------------------------
# Labour (who carries out each operation)
# ---------------------------------------------------------------------------


@frappe.whitelist()
def list_labour():
    """Every active employee, for the Labour dropdowns."""
    return search_labour(limit=1000)


@frappe.whitelist()
def search_labour(search=None, limit=10):
    """Labour picker for the operations tables: active employees."""
    txt = "%{0}%".format((search or "").strip())
    return frappe.get_all(
        "Employee",
        filters={"status": "Active"},
        or_filters={"name": ["like", txt], "employee_name": ["like", txt], "designation": ["like", txt]},
        fields=["name", "employee_name", "designation"],
        order_by="employee_name",
        limit_page_length=cint(limit) or 10,
    )


@frappe.whitelist()
def create_labour(employee_name, designation=None):
    """Add a worker from the operations table. Employee insists on a gender, a
    date of birth and a joining date; the floor rarely knows the first two at
    that moment, so placeholders go in and HR completes the record later."""
    frappe.has_permission("Employee", "create", throw=True)
    employee_name = (employee_name or "").strip()
    if not employee_name:
        frappe.throw(_("Name the worker"))
    existing = frappe.db.get_value("Employee", {"employee_name": employee_name, "status": "Active"}, "name")
    if existing:
        return {"name": existing, "employee_name": employee_name}
    parts = employee_name.split(" ", 1)
    emp = frappe.new_doc("Employee")
    emp.first_name = parts[0]
    emp.last_name = parts[1] if len(parts) > 1 else None
    emp.company = _current_company()
    emp.status = "Active"
    emp.gender = frappe.db.get_value("Gender", "Prefer not to say", "name") or frappe.db.get_value("Gender", {}, "name")
    emp.date_of_birth = "1990-01-01"
    emp.date_of_joining = frappe.utils.nowdate()
    designation = (designation or "").strip()
    if designation:
        if not frappe.db.exists("Designation", designation):
            frappe.get_doc({"doctype": "Designation", "designation_name": designation}).insert(ignore_permissions=True)
        emp.designation = designation
    emp.insert()
    frappe.db.commit()
    return {"name": emp.name, "employee_name": emp.employee_name, "designation": emp.designation}


# ---------------------------------------------------------------------------
# Options / lookups (feed the New Work Order modal)
# ---------------------------------------------------------------------------


@frappe.whitelist()
def get_work_order_options():
    """Dropdown data for the New Work Order modal: warehouses and the fixed
    production-stage list."""
    company = _current_company()
    wh_filters = {"is_group": 0, "disabled": 0, "warehouse_type": ["!=", "Transit"]}
    if company:
        wh_filters["company"] = company
    warehouses = frappe.get_all(
        "Warehouse",
        filters=wh_filters,
        fields=["name", "warehouse_name"],
        order_by="warehouse_name",
        limit_page_length=200,
    )
    wip, fg = _default_warehouses()
    return {
        "stages": STAGES,
        "warehouses": warehouses,
        "default_wip_warehouse": wip,
        "default_fg_warehouse": fg,
        "company": company,
        "currency": _company_currency(company),
    }


@frappe.whitelist()
def search_items(search=None, limit=20):
    """The 'Item to be manufactured' autocomplete.

    Items with an active BOM come first. Items without one are listed too
    (flagged `has_bom: 0`) so the page can offer to create the BOM on the spot:
    with no search text only trailer models are added, so the raw materials do
    not crowd the list; once something is typed, any stock item that matches."""
    search = (search or "").strip()
    rows = frappe.db.sql(
        """
        SELECT i.name, i.item_name, i.stock_uom, i.description,
               i.custom_is_trailer AS is_trailer,
               EXISTS (SELECT 1 FROM `tabBOM` b WHERE b.item = i.name AND b.is_active = 1) AS has_bom
        FROM `tabItem` i
        WHERE i.disabled = 0 AND i.is_stock_item = 1 AND i.has_variants = 0
          AND (i.name LIKE %(txt)s OR i.item_name LIKE %(txt)s)
        HAVING has_bom OR is_trailer OR %(searching)s
        ORDER BY has_bom DESC, is_trailer DESC, i.item_name
        LIMIT %(limit)s
        """,
        {"txt": "%{0}%".format(search), "searching": 1 if search else 0, "limit": cint(limit) or 20},
        as_dict=True,
    )
    for r in rows:
        r["default_bom"] = _default_bom_for_item(r["name"]) if r["has_bom"] else None
    return rows


@frappe.whitelist()
def search_raw_items(search=None, limit=20):
    """Any stock item -- used when adding a raw-material row to a Work Order's
    required-items table (unlike search_items, not restricted to items with a
    BOM)."""
    txt = "%{0}%".format((search or "").strip())
    return frappe.get_all(
        "Item",
        filters={"disabled": 0, "is_stock_item": 1},
        or_filters={"name": ["like", txt], "item_name": ["like", txt]},
        fields=["name", "item_name", "stock_uom", "valuation_rate"],
        order_by="item_name",
        limit_page_length=cint(limit) or 20,
    )


@frappe.whitelist()
def get_item_boms(item):
    """All active BOMs for an item, with the default flagged. Backs the BOM
    dropdown once an item is chosen."""
    if not item:
        return {"default_bom": None, "boms": []}
    boms = frappe.get_all(
        "BOM",
        filters={"item": item, "is_active": 1},
        fields=["name", "is_default", "is_active", "quantity", "uom", "total_cost", "currency"],
        order_by="is_default desc, modified desc",
    )
    return {"default_bom": _default_bom_for_item(item), "boms": boms}


@frappe.whitelist()
def get_bom_detail(bom, qty=1):
    """Light costing preview for the modal, scaled to the chosen qty."""
    if not bom or not frappe.db.exists("BOM", bom):
        return {}
    doc = frappe.get_doc("BOM", bom)
    scale = flt(qty) or 1
    per_unit = flt(doc.quantity) or 1
    factor = scale / per_unit
    rm = flt(doc.raw_material_cost) * factor
    op = flt(doc.operating_cost) * factor
    total = rm + op
    currency = doc.currency or _company_currency()
    return {
        "bom": doc.name,
        "item_name": doc.item_name,
        "n_materials": len(doc.items),
        "n_operations": len(doc.operations),
        "raw_material_cost": _money(rm, currency),
        "operating_cost": _money(op, currency),
        "total_cost": _money(total, currency),
        "per_unit": _money(total / scale if scale else 0, currency),
    }


# ---------------------------------------------------------------------------
# New item / new BOM pop-ups on the New Work Order modal
# ---------------------------------------------------------------------------


@frappe.whitelist()
def get_master_options():
    """Dropdown data for the New Item and New BOM pop-ups."""
    from a3_trading_management.integrations.item import next_item_code

    return {
        "item_groups": frappe.get_all("Item Group", filters={"is_group": 0}, pluck="name", order_by="name"),
        "uoms": frappe.get_all("UOM", filters={"enabled": 1}, pluck="name", order_by="name"),
        "operations": frappe.get_all("Operation", fields=["name", "workstation"], order_by="name"),
        "workstations": frappe.get_all("Workstation", fields=["name", "hour_rate"], order_by="name"),
        "next_item_code": next_item_code(),
    }


@frappe.whitelist()
def create_item(item_name, item_code=None, item_group=None, stock_uom=None, is_trailer=0,
                rate=None, description=None):
    """Create a stock Item from the portal.

    A trailer model gets `rate` as its selling price and Finished Goods as its
    default yard; anything else (a raw material or part) gets `rate` as its buying
    price and valuation rate, and the raw-material yard. Leaving the code empty
    numbers the item from the Item series (integrations.item.before_naming)."""
    frappe.has_permission("Item", "create", throw=True)
    item_name = (item_name or "").strip()
    if not item_name:
        frappe.throw(_("Item name is required"))
    existing = frappe.db.get_value("Item", {"item_name": item_name, "disabled": 0}, "name")
    if existing:
        frappe.throw(_("An item called {0} already exists ({1}).").format(item_name, existing))
    item_code = (item_code or "").strip()
    if item_code and frappe.db.exists("Item", item_code):
        frappe.throw(_("Item code {0} is already in use.").format(item_code))
    if not item_code:
        from a3_trading_management.integrations.item import next_item_code

        # A code matching the series is re-allocated from the counter at insert,
        # so two people creating items at once cannot collide.
        item_code = next_item_code() or item_name

    trailer = cint(is_trailer)
    rate = flt(rate)
    wip, fg = _default_warehouses()
    doc = frappe.new_doc("Item")
    doc.item_code = item_code
    doc.item_name = item_name
    doc.description = (description or "").strip() or item_name
    doc.item_group = item_group or frappe.db.get_value("Item Group", {"is_group": 0}, "name")
    doc.stock_uom = stock_uom or "Nos"
    doc.is_stock_item = 1
    doc.include_item_in_manufacturing = 0 if trailer else 1
    doc.custom_is_trailer = trailer
    if not trailer and rate:
        doc.valuation_rate = rate
    company = _current_company()
    default_yard = fg if trailer else wip
    if company and default_yard:
        doc.append("item_defaults", {"company": company, "default_warehouse": default_yard})
    doc.insert()

    if rate:
        price_list = frappe.db.get_value("Price List", {"selling" if trailer else "buying": 1, "enabled": 1},
                                         "name")
        if price_list:
            frappe.get_doc({
                "doctype": "Item Price", "item_code": doc.name, "price_list": price_list,
                "price_list_rate": rate, "currency": _company_currency(company),
            }).insert(ignore_permissions=True)
    frappe.db.commit()
    return {"name": doc.name, "item_name": doc.item_name, "stock_uom": doc.stock_uom,
            "valuation_rate": doc.valuation_rate, "is_trailer": trailer}


@frappe.whitelist()
def search_operations(search=None, limit=10):
    """Operation picker: each operation with its default workstation and that
    workstation's hour rate, so picking one can fill the whole row."""
    txt = "%{0}%".format((search or "").strip())
    return frappe.db.sql(
        """
        SELECT o.name, o.workstation, COALESCE(w.hour_rate, 0) AS hour_rate
        FROM `tabOperation` o
        LEFT JOIN `tabWorkstation` w ON w.name = o.workstation
        WHERE o.name LIKE %(txt)s
        ORDER BY o.name
        LIMIT %(limit)s
        """,
        {"txt": txt, "limit": cint(limit) or 10},
        as_dict=True,
    )


@frappe.whitelist()
def search_workstations(search=None, limit=10):
    """Workstation picker, with the hour rate an operation there is costed at."""
    txt = "%{0}%".format((search or "").strip())
    return frappe.get_all(
        "Workstation",
        filters={"name": ["like", txt]},
        fields=["name", "hour_rate"],
        order_by="name",
        limit_page_length=cint(limit) or 10,
    )


@frappe.whitelist()
def create_workstation(name, hour_rate=0):
    """A workstation from the portal. The rate is booked as labour: ERPNext's
    hour rate is the sum of its labour, electricity, consumable and rent parts."""
    frappe.has_permission("Workstation", "create", throw=True)
    name = (name or "").strip()
    if not name:
        frappe.throw(_("Workstation name is required"))
    if frappe.db.exists("Workstation", name):
        frappe.throw(_("Workstation {0} already exists").format(name))
    ws = frappe.new_doc("Workstation")
    ws.workstation_name = name
    ws.hour_rate_labour = flt(hour_rate)
    ws.insert()
    frappe.db.commit()
    return {"name": ws.name, "hour_rate": flt(ws.hour_rate)}


@frappe.whitelist()
def create_operation(name, workstation=None):
    """An operation from the portal, with the workstation it normally runs at."""
    frappe.has_permission("Operation", "create", throw=True)
    name = (name or "").strip()
    if not name:
        frappe.throw(_("Operation name is required"))
    if frappe.db.exists("Operation", name):
        frappe.throw(_("Operation {0} already exists").format(name))
    workstation = (workstation or "").strip() or None
    if workstation and not frappe.db.exists("Workstation", workstation):
        frappe.throw(_("Workstation {0} does not exist").format(workstation))
    op = frappe.new_doc("Operation")
    op.workstation = workstation
    op.insert(set_name=name)
    frappe.db.commit()
    return {
        "name": op.name,
        "workstation": workstation,
        "hour_rate": flt(frappe.db.get_value("Workstation", workstation, "hour_rate")) if workstation else 0,
    }


@frappe.whitelist()
def create_bom(item, items, operations=None, quantity=1):
    """Create and submit a BOM from the pop-up: the parts list for `quantity` units
    of `item`, and optionally the operations with minutes. It becomes the item's
    default BOM, so the work order form picks it straight up. Someone who may not
    submit a BOM (the Supervisor) gets a draft instead, for the manager to approve."""
    return save_bom(item, items, operations, quantity, submit=1, set_default=1)


def _bom_material_rows(item, items):
    rows = items if isinstance(items, list) else json.loads(items or "[]")
    rows = [r for r in rows if (r.get("item_code") or "").strip() and flt(r.get("qty")) > 0]
    if not rows:
        frappe.throw(_("Add at least one raw material with a quantity"))
    if any(r["item_code"] == item for r in rows):
        frappe.throw(_("{0} cannot be a raw material of its own BOM").format(item))
    return rows


def _bom_operation_rows(operations):
    ops = operations if isinstance(operations, list) else json.loads(operations or "[]")
    ops = [o for o in ops if (o.get("operation") or "").strip()]
    for o in ops:
        if flt(o.get("time_in_mins")) <= 0:
            frappe.throw(_("Operation {0} needs its time in minutes").format(o["operation"]))
        # ERPNext insists on a workstation per operation; a known operation brings its own.
        if not (o.get("workstation") or "").strip() and not frappe.db.get_value(
            "Operation", o["operation"].strip(), "workstation"
        ):
            frappe.throw(_("Choose a workstation for operation {0}").format(o["operation"]))
    return ops


def _conversion_factor(item_code, uom):
    """Stock units in one `uom` of the item (1 for its stock UOM)."""
    from erpnext.stock.get_item_details import get_conversion_factor

    return flt(get_conversion_factor(item_code, uom).get("conversion_factor")) or 1


def _sheet_cut_fields(r, stock_uom):
    """A sheet row carries its cut shape and sizes; the shape's formulas give the
    rectangle it is cut and charged as, and the row qty is that area times the
    pieces, in the stock unit (cm2)."""
    from a3_trading_management.api.sheet_cut import _dims, is_sheet_item, legacy_dims, shape_box

    if not is_sheet_item(r["item_code"]):
        return {}
    shape = r.get("cut_shape")
    dims = _dims(r.get("cut_dims")) or (legacy_dims(shape, r.get("cut_length_cm"), r.get("cut_width_cm")) if shape else {})
    if not shape or not dims:
        frappe.throw(_("{0} is a sheet: set the size to cut with the scissors on its row").format(r["item_code"]))
    l, w = shape_box(shape, dims)
    pieces = max(1, cint(r.get("cut_pieces")) or 1)
    return {
        "custom_cut_shape": shape, "custom_cut_dims": json.dumps(dims), "custom_cut_length_cm": l,
        "custom_cut_width_cm": w, "custom_cut_pieces": pieces,
        "qty": l * w * pieces, "uom": stock_uom, "conversion_factor": 1,
    }


@frappe.whitelist()
def save_bom(item, items, operations=None, quantity=1, name=None, submit=0, set_default=1):
    """Create a BOM, or rewrite a draft one (`name`), from the BOM form.

    Material rows are {item_code, qty, uom}: a row in another unit than the item's
    stock UOM (2 Meter of an item kept in Meter, or bought in Roll) is costed through
    the item's UOM conversion. With `submit`, someone allowed to submit a BOM gets it
    submitted (and made the item's default with `set_default`); anyone else gets a
    draft for the manager. A submitted BOM is never rewritten: copy it instead."""
    can_submit = frappe.has_permission("BOM", "submit")
    if not item or not frappe.db.exists("Item", item):
        frappe.throw(_("Choose the item this BOM builds"))
    rows = _bom_material_rows(item, items)
    ops = _bom_operation_rows(operations)

    if name:
        bom = frappe.get_doc("BOM", name)
        bom.check_permission("write")
        if bom.docstatus != 0:
            frappe.throw(_("{0} is approved and cannot be changed. Copy it to make a new version.").format(name))
        bom.set("items", [])
        bom.set("operations", [])
    else:
        frappe.has_permission("BOM", "create", throw=True)
        bom = frappe.new_doc("BOM")
        bom.company = _current_company()
        bom.currency = _company_currency(bom.company)
        # Materials are costed at their average purchase rate (api/avg_rate.py).
        from a3_trading_management.api.avg_rate import AVERAGE_PRICE_LIST, ensure_price_list

        bom.rm_cost_as_per = "Price List"
        bom.buying_price_list = ensure_price_list()
        bom.price_list_currency = frappe.db.get_value("Price List", AVERAGE_PRICE_LIST, "currency")
        bom.plc_conversion_rate = 1
        bom.is_active = 1
    bom.item = item
    bom.quantity = flt(quantity) or 1
    bom.is_default = 1 if (can_submit and cint(submit) and cint(set_default)) else 0
    bom.with_operations = 1 if ops else 0
    for r in rows:
        stock_uom = frappe.get_cached_value("Item", r["item_code"], "stock_uom")
        uom = (r.get("uom") or "").strip() or stock_uom
        row = {
            "item_code": r["item_code"], "qty": flt(r["qty"]), "uom": uom,
            "conversion_factor": 1 if uom == stock_uom else _conversion_factor(r["item_code"], uom),
        }
        row.update(_sheet_cut_fields(r, stock_uom))
        bom.append("items", row)
    for o in ops:
        operation = _ensure_operation(o["operation"])
        workstation = _ensure_workstation(
            o.get("workstation") or frappe.db.get_value("Operation", operation, "workstation")
        )
        bom.append("operations", {
            "operation": operation, "workstation": workstation, "time_in_mins": flt(o["time_in_mins"]),
            "custom_labour": _labour(o.get("labour")),
        })
    bom.save()
    if can_submit and cint(submit):
        bom.submit()
    frappe.db.commit()
    return {"name": bom.name, "submitted": bom.docstatus == 1, "total_cost": _money(bom.total_cost, bom.currency)}


def _sheet_dims(i):
    from a3_trading_management.api.sheet_cut import _dims, legacy_dims

    if not i.get("custom_cut_shape"):
        return {}
    return _dims(i.get("custom_cut_dims")) or legacy_dims(
        i.custom_cut_shape, i.get("custom_cut_length_cm"), i.get("custom_cut_width_cm"))


@frappe.whitelist()
def get_bom_form(name):
    """Everything the BOM form shows: header, materials with unit and cost,
    operations with time and cost, and the cost totals."""
    bom = frappe.get_doc("BOM", name)
    bom.check_permission("read")
    cur = bom.currency or _company_currency(bom.company)
    return {
        "name": bom.name, "item": bom.item, "item_name": bom.item_name, "quantity": flt(bom.quantity),
        "docstatus": bom.docstatus, "is_default": bom.is_default, "is_active": bom.is_active,
        "can_submit": bool(frappe.has_permission("BOM", "submit")),
        "items": [{
            "item_code": i.item_code, "item_name": i.item_name, "qty": flt(i.qty), "uom": i.uom,
            "stock_uom": i.stock_uom, "conversion_factor": flt(i.conversion_factor) or 1,
            "rate": flt(i.rate), "amount": flt(i.amount),
            "cut_shape": i.get("custom_cut_shape"), "cut_length_cm": flt(i.get("custom_cut_length_cm")),
            "cut_width_cm": flt(i.get("custom_cut_width_cm")), "cut_pieces": cint(i.get("custom_cut_pieces")),
            "cut_dims": _sheet_dims(i),
        } for i in bom.items],
        "operations": [{
            "operation": o.operation, "workstation": o.workstation, "time_in_mins": flt(o.time_in_mins),
            "hour_rate": flt(o.hour_rate), "operating_cost": flt(o.operating_cost), "labour": o.get("custom_labour"),
        } for o in bom.operations],
        "raw_material_cost": flt(bom.raw_material_cost), "operating_cost": flt(bom.operating_cost),
        "total_cost": flt(bom.total_cost), "currency": cur,
    }


@frappe.whitelist()
def get_item_uoms(item):
    """The units a material can be entered in on a BOM line (its stock UOM first,
    then its UOM conversions), with the valuation rate per stock unit the BOM
    costs it at."""
    from erpnext.manufacturing.doctype.bom.bom import get_valuation_rate

    frappe.has_permission("Item", "read", throw=True)
    stock_uom = frappe.db.get_value("Item", item, "stock_uom")
    uoms = [{"uom": stock_uom, "conversion_factor": 1}]
    for d in frappe.get_all("UOM Conversion Detail", filters={"parent": item, "parenttype": "Item"},
                            fields=["uom", "conversion_factor"], order_by="idx"):
        if d.uom != stock_uom:
            uoms.append({"uom": d.uom, "conversion_factor": flt(d.conversion_factor)})
    sheet = frappe.db.get_value(
        "Item", item, ["custom_cut_type", "custom_sheet_length_cm", "custom_sheet_width_cm"], as_dict=True
    ) or {}
    return {
        "stock_uom": stock_uom, "uoms": uoms,
        # The rate a BOM costs it at: the average purchase rate, else valuation.
        "valuation_rate": _bom_rate(item) or get_valuation_rate({"item_code": item, "company": _current_company()}),
        "is_sheet": sheet.get("custom_cut_type") == "Sheet",
        "sheet_length": flt(sheet.get("custom_sheet_length_cm")), "sheet_width": flt(sheet.get("custom_sheet_width_cm")),
    }


def _bom_rate(item):
    from a3_trading_management.api.avg_rate import AVERAGE_PRICE_LIST

    return flt(frappe.db.get_value(
        "Item Price", {"price_list": AVERAGE_PRICE_LIST, "item_code": item}, "price_list_rate"))


@frappe.whitelist()
def submit_bom(name, set_default=1):
    """The manager approves a draft BOM, by default making it the item's BOM."""
    bom = frappe.get_doc("BOM", name)
    bom.check_permission("submit")
    bom.is_default = cint(set_default)
    bom.submit()
    frappe.db.commit()
    return {"name": bom.name, "is_default": bom.is_default}


@frappe.whitelist()
def set_default_bom(name):
    """Make an approved BOM the one new work orders for its item use. ERPNext
    clears the flag on the item's other BOMs and updates the item's default."""
    bom = frappe.get_doc("BOM", name)
    bom.check_permission("submit")
    if bom.docstatus != 1:
        frappe.throw(_("Approve {0} before making it the default").format(name))
    bom.is_active = 1
    bom.is_default = 1
    bom.save()
    frappe.db.commit()
    return {"name": bom.name}


# ---------------------------------------------------------------------------
# Before creating: is the material there, and is this for a customer order?
# ---------------------------------------------------------------------------


def _bin_qty(item_code, warehouse):
    if warehouse:
        return flt(frappe.db.get_value("Bin", {"item_code": item_code, "warehouse": warehouse}, "actual_qty"))
    row = frappe.db.sql("select coalesce(sum(actual_qty), 0) from `tabBin` where item_code = %s", item_code)
    return flt(row[0][0]) if row else 0.0


@frappe.whitelist()
def check_material(bom, qty=1, source_warehouse=None):
    """Whether the yard holds what the BOM needs for `qty`: one row per raw
    material with needed / available / short, so a shortfall is visible before
    the order exists rather than at the last stage."""
    if not bom or not frappe.db.exists("BOM", bom):
        return {"warehouse": source_warehouse, "rows": [], "short_items": 0, "ok": True}
    doc = frappe.get_doc("BOM", bom)
    factor = (flt(qty) or 1) / (flt(doc.quantity) or 1)
    warehouse = source_warehouse or _default_warehouses()[0]
    from a3_trading_management.api.live_stock import _items_on_open_mr
    requested = _items_on_open_mr()
    rows = []
    for d in doc.items:
        needed = (flt(d.stock_qty) or flt(d.qty)) * factor
        available = _bin_qty(d.item_code, warehouse)
        rows.append({
            "item_code": d.item_code,
            "item_name": d.item_name or d.item_code,
            "uom": d.stock_uom or d.uom,
            "needed": needed,
            "available": available,
            "short": max(needed - available, 0.0),
            "requested": d.item_code in requested,
        })
    short_items = sum(1 for r in rows if r["short"] > 0)
    return {"warehouse": warehouse, "rows": rows, "short_items": short_items, "ok": short_items == 0}


@frappe.whitelist()
def request_shortfall(bom, qty=1, source_warehouse=None):
    """Raise a Purchase Material Request for exactly what the yard is short of."""
    chk = check_material(bom, qty, source_warehouse)
    items = [{"item_code": r["item_code"], "qty": r["short"]} for r in chk["rows"] if r["short"] > 0]
    if not items:
        frappe.throw(_("Nothing is short — the yard holds everything this order needs."))
    from a3_trading_management.api.live_stock import create_material_request
    return create_material_request(items=items, force=0)


@frappe.whitelist()
def open_sales_orders_for_item(item):
    """Confirmed customer orders for `item` with trailers still to build -- what
    the 'For sales order' box offers. `to_build` is the ordered quantity not yet
    covered by a work order."""
    if not item:
        return []
    rows = frappe.db.sql(
        """
        select so.name, so.customer, so.customer_name, so.delivery_date,
               sum(soi.qty) as qty, sum(soi.delivered_qty) as delivered_qty,
               (select coalesce(sum(wo.qty), 0) from `tabWork Order` wo
                 where wo.sales_order = so.name and wo.production_item = %(item)s and wo.docstatus < 2) as ordered_qty,
               (select count(*) from `tabTrailer Serial` ts
                 where ts.sales_order = so.name and ts.trailer_type = %(item)s and ts.status = 'Sold') as reserved_qty
        from `tabSales Order` so
        join `tabSales Order Item` soi on soi.parent = so.name
        where so.docstatus = 1 and so.skip_delivery_note = 0
          and so.status not in ('Closed', 'Completed', 'Cancelled', 'On Hold')
          and soi.item_code = %(item)s
        group by so.name
        order by so.delivery_date asc, so.name asc
        """,
        {"item": item}, as_dict=True,
    )
    out = []
    for r in rows:
        # Still to build = ordered, less delivered, less reserved from stock, less
        # already on a work order. An order fully covered is not offered.
        to_build = flt(r.qty) - flt(r.delivered_qty) - flt(r.reserved_qty) - flt(r.ordered_qty)
        if to_build <= 0:
            continue
        out.append({
            "name": r.name,
            "customer_name": r.customer_name or r.customer,
            "delivery_date": formatdate(r.delivery_date, "dd MMM yyyy") if r.delivery_date else "",
            "qty": flt(r.qty),
            "to_build": to_build,
        })
    return out


# ---------------------------------------------------------------------------
# Create
# ---------------------------------------------------------------------------


@frappe.whitelist()
def create_work_order(
    item,
    qty=1,
    bom=None,
    wip_warehouse=None,
    fg_warehouse=None,
    source_warehouse=None,
    planned_start_date=None,
    expected_delivery_date=None,
    description=None,
    sales_order=None,
):
    """Create a Draft Work Order for `item`, seed required_items + operations
    from its BOM, stamp the first production stage, and return its name.

    `source_warehouse` is the yard raw material is taken from (`wip_warehouse` is
    accepted as its older name). `sales_order` makes this a build-to-order: the
    order is carried on the work order and, on completion, the finished trailers
    are reserved to it (serial_control). `description` is the specification the
    floor works to; it prints on the traveler."""
    if not item:
        frappe.throw(_("Item to be manufactured is required"))
    if sales_order:
        if frappe.db.get_value("Sales Order", sales_order, "docstatus") != 1:
            frappe.throw(_("Sales Order {0} must be confirmed before building for it").format(sales_order))

    bom = bom or _default_bom_for_item(item)
    if not bom:
        frappe.throw(_("No active BOM found for {0}").format(item))

    company = _current_company()
    def_wip, def_fg = _default_warehouses()
    wip = source_warehouse or wip_warehouse or def_wip
    fg = fg_warehouse or def_fg
    if not fg:
        frappe.throw(_("No finished-goods warehouse is configured"))

    _ensure_editable_setting()

    wo = frappe.new_doc("Work Order")
    wo.production_item = item
    wo.qty = flt(qty) or 1
    wo.bom_no = bom
    wo.company = company
    wo.fg_warehouse = fg
    wo.wip_warehouse = wip
    wo.source_warehouse = wip  # raw materials consumed straight from here
    wo.skip_transfer = 1  # no separate material-transfer step; Manufacture consumes RM directly
    wo.use_multi_level_bom = 0
    if description:
        wo.description = description
    if sales_order:
        wo.sales_order = sales_order
    if planned_start_date:
        wo.planned_start_date = get_datetime(planned_start_date)
    if expected_delivery_date:
        wo.expected_delivery_date = getdate(expected_delivery_date)

    # Pull raw materials + operations from the BOM (server-side; no form JS runs).
    wo.get_items_and_operations_from_bom()
    _copy_labour_from_bom(wo)

    wo.custom_production_stage = "Material"
    wo.append(
        "custom_stage_history",
        {
            "stage": "Material",
            "from_stage": "",
            "entered_on": now_datetime(),
            "entered_by": frappe.session.user,
            "duration_hours": 0,
            "notes": "Work order created" + (" for {0}".format(sales_order) if sales_order else ""),
        },
    )

    wo.flags.ignore_permissions = True
    wo.insert(ignore_permissions=True)
    return {"name": wo.name, "stage": "Material", "sales_order": sales_order}


# ---------------------------------------------------------------------------
# Read: dashboard KPIs + list + detail
# ---------------------------------------------------------------------------


def _wo_rows():
    """Raw Work Order rows carrying the fields both the list and dashboard need."""
    return frappe.get_all(
        "Work Order",
        fields=[
            "name",
            "production_item",
            "item_name",
            "qty",
            "produced_qty",
            "status",
            "docstatus",
            "bom_no",
            "custom_production_stage",
            "planned_start_date",
            "expected_delivery_date",
            "total_operating_cost",
            "additional_operating_cost",
            "modified",
        ],
        order_by="modified desc",
        limit_page_length=500,
    )


def _rm_cost_map(names):
    """{work_order: planned raw-material cost} in one grouped query."""
    if not names:
        return {}
    rows = frappe.get_all(
        "Work Order Item",
        filters={"parent": ["in", names]},
        fields=["parent", "sum(amount) as amount"],
        group_by="parent",
    )
    return {r.parent: flt(r.amount) for r in rows}


def _progress(stage, docstatus):
    if docstatus == 2:
        return 0
    idx = _stage_index(stage)
    if stage == "Delivery":
        return 100
    return int(round(idx / len(STAGES) * 100))


@frappe.whitelist()
def list_work_orders(search=None):
    """Rows for the left-hand Work Orders list."""
    rows = _wo_rows()
    q = (search or "").strip().lower()
    out = []
    for r in rows:
        stage = r.custom_production_stage or "Material"
        label = r.item_name or r.production_item or r.name
        if q and q not in (label + " " + (r.name or "")).lower():
            continue
        out.append(
            {
                "name": r.name,
                "item": r.production_item,
                "item_name": label,
                "qty": flt(r.qty),
                "produced_qty": flt(r.produced_qty),
                "stage": stage,
                "stage_index": _stage_index(stage),
                "status": stage,
                "badge": _STAGE_BADGE.get(stage, "badge--soft"),
                "progress": _progress(stage, r.docstatus),
                "bom": r.bom_no,
            }
        )
    return out


@frappe.whitelist()
def get_dashboard():
    """The six production KPI number cards, computed live."""
    rows = _wo_rows()
    rm_map = _rm_cost_map([r.name for r in rows])
    currency = _company_currency()
    month_start = getdate().replace(day=1)

    open_wo = in_prod = ready = delivered_mtd = 0
    wip_value = 0.0
    on_time_hits = on_time_total = 0

    for r in rows:
        if r.docstatus == 2:
            continue
        stage = r.custom_production_stage or "Material"
        rm = rm_map.get(r.name, 0.0)
        prod_cost = rm + flt(r.total_operating_cost) + flt(r.additional_operating_cost)

        if stage == "Material":
            open_wo += 1
        elif stage in ("Fabrication", "Assembly", "Paint"):
            in_prod += 1
        elif stage == "QC":
            ready += 1

        if stage != "Delivery":
            wip_value += prod_cost
        else:
            if getdate(r.modified) >= month_start:
                delivered_mtd += 1
            # On-time = delivered on/before the expected date.
            if r.expected_delivery_date:
                on_time_total += 1
                if getdate(r.modified) <= getdate(r.expected_delivery_date):
                    on_time_hits += 1

    on_time_pct = int(round(on_time_hits / on_time_total * 100)) if on_time_total else 100

    from a3_trading_management.setup.supervisor import is_supervisor_only

    cards = [
            {"key": "open", "label": "Open Work Orders", "value": open_wo},
            {"key": "prod", "label": "In Production", "value": in_prod},
            {"key": "ready", "label": "Ready for Delivery", "value": ready},
            {"key": "delivered", "label": "Delivered (MTD)", "value": delivered_mtd},
            {"key": "wip", "label": "WIP Value", "value": _money(wip_value, currency)},
            {"key": "ontime", "label": "On-time %", "value": "{0}%".format(on_time_pct)},
    ]
    if is_supervisor_only():
        cards = [c for c in cards if c["key"] != "wip"]  # money
    return {"cards": cards}


@frappe.whitelist()
def get_work_order(name):
    """Full detail for the right-hand production panel."""
    wo = frappe.get_doc("Work Order", name)
    currency = _company_currency(wo.company)
    stage = wo.custom_production_stage or "Material"
    editable = wo.docstatus == 0

    required_items = [
        {
            "idx": d.idx,
            "item_code": d.item_code,
            "item_name": d.item_name or d.item_code,
            "qty": flt(d.required_qty),
            "uom": d.stock_uom,
            "rate": flt(d.rate),
            "rate_fmt": _money(d.rate, currency),
            "amount": flt(d.amount),
            "amount_fmt": _money(d.amount, currency),
            "source_warehouse": d.source_warehouse,
        }
        for d in wo.required_items
    ]
    # A sheet row's cut size: its own, or the BOM's for a row that came from it.
    from a3_trading_management.api.sheet_cut import wo_row_cuts

    found = {}
    for d, c in wo_row_cuts(wo):
        found.setdefault(d.idx, []).append(c)
    for r in required_items:
        if r["idx"] in found:
            cuts = found[r["idx"]]
            r["is_sheet"] = True
            if len(cuts) == 1 and cuts[0]:
                c = cuts[0]
                r["cut"] = {"shape": c["custom_cut_shape"] or "Rectangle", "l": flt(c["custom_cut_length_cm"]),
                            "w": flt(c["custom_cut_width_cm"]) or flt(c["custom_cut_length_cm"]),
                            "pieces": cint(c["custom_cut_pieces"]) or 1,
                            "dims": json.loads(c["custom_cut_dims"]) if c["custom_cut_dims"] else {}}
            elif len(cuts) > 1:
                r["cut_note"] = _("{0} cut sizes from the BOM").format(len(cuts))

    # Labour the BOM assigns to each step: shown (and saved on the next save) for
    # any operation the work order has not assigned yet, including orders raised
    # before the BOM had labour on it.
    bom_labour = {}
    if wo.bom_no:
        for b in frappe.get_all("BOM Operation", filters={"parent": wo.bom_no, "parenttype": "BOM"},
                                fields=["idx", "operation", "custom_labour", "custom_more_labour"],
                                order_by="idx"):
            if b.custom_labour:
                bom_labour.setdefault(("idx", b.idx, b.operation), _labour_ids(b))
                bom_labour.setdefault(("op", b.operation), _labour_ids(b))

    def _op_labour(d):
        if d.get("custom_labour"):
            return _labour_ids(d), False
        found = bom_labour.get(("idx", d.idx, d.operation)) or bom_labour.get(("op", d.operation))
        return (found, True) if found else ([], False)

    names = {}

    def _name(e):
        if e not in names:
            names[e] = frappe.db.get_value("Employee", e, "employee_name") or e
        return names[e]

    operations = []
    for d in wo.operations:
        labs, from_bom = _op_labour(d)
        operations.append({
            "idx": d.idx,
            "operation": d.operation,
            "workstation": d.workstation,
            "time_in_mins": flt(d.time_in_mins),
            "hours": round(flt(d.time_in_mins) / 60.0, 2),
            "hour_rate": flt(d.hour_rate),
            "hour_rate_fmt": _money(d.hour_rate, currency),
            "operating_cost": flt(d.planned_operating_cost),
            "operating_cost_fmt": _money(d.planned_operating_cost, currency),
            "labour": labs[0] if labs else "",
            "labours": labs,
            "labour_names": [_name(e) for e in labs],
            "labour_name": ", ".join(_name(e) for e in labs),
            "labour_from_bom": from_bom,
        })

    chassis_numbers = frappe.get_all(
        "Trailer Serial", filters={"work_order": wo.name}, pluck="chassis_number", order_by="creation asc"
    )

    rm_cost = sum(flt(d.amount) for d in wo.required_items)
    op_cost = flt(wo.total_operating_cost)
    add_cost = flt(wo.additional_operating_cost)
    total = rm_cost + op_cost + add_cost
    per_unit = total / flt(wo.qty) if flt(wo.qty) else 0

    history = [
        {
            "idx": h.idx,
            "entered_on_raw": h.entered_on and get_datetime(h.entered_on).strftime("%Y-%m-%dT%H:%M"),
            "stage": h.stage,
            "from_stage": h.from_stage or "—",
            "entered_on": frappe.utils.format_datetime(h.entered_on, "dd MMM yyyy, HH:mm")
            if h.entered_on
            else "—",
            "entered_by": h.entered_by,
            "duration_hours": round(flt(h.duration_hours), 2),
            "notes": h.notes or "",
        }
        for h in wo.custom_stage_history
    ]

    stock_entry = frappe.db.get_value(
        "Stock Entry",
        {"work_order": wo.name, "purpose": "Manufacture", "docstatus": 1},
        "name",
    )

    from a3_trading_management.api.quality import latest_inspection

    return {
        "name": wo.name,
        "quality": latest_inspection("Work Order", wo.name),
        "sales_order": wo.sales_order,
        "customer": (frappe.db.get_value("Sales Order", wo.sales_order, "customer_name") if wo.sales_order else None),
        "description": wo.description,
        "source_warehouse": wo.source_warehouse or wo.wip_warehouse,
        "item": wo.production_item,
        "item_name": wo.item_name or wo.production_item,
        "qty": flt(wo.qty),
        "produced_qty": flt(wo.produced_qty),
        "bom": wo.bom_no,
        "stage": stage,
        "stage_index": _stage_index(stage),
        "stages": STAGES,
        "badge": _STAGE_BADGE.get(stage, "badge--soft"),
        "progress": _progress(stage, wo.docstatus),
        "status": stage,
        "erp_status": wo.status,
        "docstatus": wo.docstatus,
        "editable": editable,
        "is_final": stage == "Delivery",
        "next_stage": STAGES[_stage_index(stage) + 1] if _stage_index(stage) < len(STAGES) - 1 else None,
        "wip_warehouse": wo.wip_warehouse,
        "fg_warehouse": wo.fg_warehouse,
        "chassis_numbers": [c for c in chassis_numbers if c],
        "deliveries": _trailer_deliveries(wo.name),
        "planned_start": frappe.utils.format_datetime(wo.planned_start_date, "dd MMM yyyy")
        if wo.planned_start_date
        else "—",
        "target": frappe.utils.formatdate(wo.expected_delivery_date, "dd MMM yyyy")
        if wo.expected_delivery_date
        else "—",
        "description": wo.description or "",
        "required_items": required_items,
        "operations": operations,
        "stage_history": history,
        # Reached Delivery but nothing posted (material was short): offer to post it.
        "stock_pending": bool(wo.docstatus == 1 and stage == "Delivery" and not stock_entry),
        "shortages": _shortages(wo) if (wo.docstatus == 1 and stage == "Delivery" and not stock_entry) else [],
        "costing": {
            "raw_material": _money(rm_cost, currency),
            "operating": _money(op_cost, currency),
            "additional": _money(add_cost, currency),
            "total": _money(total, currency),
            "per_unit": _money(per_unit, currency),
        },
        "stock_entry": stock_entry,
        "currency": currency,
        "sheet_pieces": _sheet_pieces(wo),
    }


def _sheet_pieces(wo):
    from a3_trading_management.api.sheet_cut import cut_summary

    return cut_summary(wo) if wo.get("custom_sheet_plan") else []


# ---------------------------------------------------------------------------
# Edit tables (only while the Work Order is in Draft)
# ---------------------------------------------------------------------------


@frappe.whitelist()
def update_work_order_tables(name, required_items=None, operations=None):
    """Replace the raw-materials and/or operations tables on a Draft Work Order.
    Called on each stage while the operator refines what was actually used."""
    wo = frappe.get_doc("Work Order", name)
    if wo.docstatus != 0:
        frappe.throw(_("Work order is finalized; its BOM tables can no longer be edited"))

    _ensure_editable_setting()

    # A Supervisor's screen has no rates, so what it sends back is not a rate: keep
    # the row's rate, or for a new row the item's valuation / workstation's hour rate.
    from a3_trading_management.setup.supervisor import is_supervisor_only

    keep_rates = is_supervisor_only()
    old_rates = {r.item_code: r.rate for r in wo.required_items}
    old_hour_rates = {(r.operation, r.workstation): r.hour_rate for r in wo.operations}

    if required_items is not None:
        rows = required_items if isinstance(required_items, list) else json.loads(required_items)
        if keep_rates:
            for r in rows:
                code = (r.get("item_code") or "").strip()
                r["rate"] = old_rates.get(code) if code in old_rates else frappe.db.get_value("Item", code, "valuation_rate")
        from a3_trading_management.api.sheet_cut import is_sheet_item, plan_doc

        def sheet_rows():
            return sorted((d.item_code, flt(d.required_qty, 2)) for d in wo.required_items if is_sheet_item(d.item_code))

        before_sheets = sheet_rows()
        wo.set("required_items", [])
        for r in rows:
            item_code = (r.get("item_code") or "").strip()
            if not item_code:
                continue
            qty = flt(r.get("qty"))
            rate = flt(r.get("rate"))
            cut = {}
            if is_sheet_item(item_code) and isinstance(r.get("cut"), dict) and flt(r["cut"].get("l")) > 0:
                # A sheet is used by cut size: the quantity is the area of the pieces.
                c = r["cut"]
                length, width = flt(c.get("l")), flt(c.get("w")) or flt(c.get("l"))
                pieces = max(1, cint(c.get("pieces")))
                cut = {"custom_cut_shape": c.get("shape") or "Rectangle", "custom_cut_length_cm": length,
                       "custom_cut_width_cm": width, "custom_cut_pieces": pieces,
                       "custom_cut_dims": json.dumps(c.get("dims") or {})}
                qty = flt(length * width * pieces, 4)
            wo.append(
                "required_items",
                {
                    **cut,
                    "item_code": item_code,
                    "item_name": r.get("item_name")
                    or frappe.db.get_value("Item", item_code, "item_name"),
                    "required_qty": qty,
                    "stock_uom": r.get("uom")
                    or frappe.db.get_value("Item", item_code, "stock_uom"),
                    "rate": rate,
                    "amount": qty * rate,
                    "source_warehouse": r.get("source_warehouse") or wo.source_warehouse,
                },
            )

        if plan_doc(wo.get("custom_sheet_plan"))["cut"] and sheet_rows() != before_sheets:
            frappe.throw(_("The sheet pieces for this work order are already cut, so its sheet rows "
                           "can no longer change."), title=_("Sheet material"))

    if operations is not None:
        rows = operations if isinstance(operations, list) else json.loads(operations)
        wo.set("operations", [])
        for r in rows:
            op = _ensure_operation(r.get("operation"))
            if not op:
                continue
            workstation = _ensure_workstation(r.get("workstation"))
            hour_rate = flt(r.get("hour_rate"))
            if keep_rates:
                hour_rate = flt(old_hour_rates.get((op, workstation))
                                or frappe.db.get_value("Workstation", workstation, "hour_rate"))
            wo.append(
                "operations",
                {
                    "operation": op,
                    "workstation": workstation,
                    "time_in_mins": flt(r.get("time_in_mins")),
                    "hour_rate": hour_rate,
                    **_labour_fields(r.get("labours"), r.get("labour")),
                },
            )

    wo.flags.ignore_permissions = True
    wo.save(ignore_permissions=True)
    return get_work_order(name)


@frappe.whitelist()
def set_operation_labour(name, operations):
    """Who worked on each operation, at any stage: also after the work order is
    submitted (the labour fields are allow_on_submit). `operations` is
    [{idx, labours: [employee, ...]}]."""
    wo = frappe.get_doc("Work Order", name)
    if wo.docstatus == 2:
        frappe.throw(_("Work order {0} is cancelled").format(name))
    rows = operations if isinstance(operations, list) else json.loads(operations)
    by_idx = {d.idx: d for d in wo.operations}
    for r in rows:
        d = by_idx.get(cint(r.get("idx")))
        if not d:
            continue
        vals = _labour_fields(r.get("labours") or [])
        vals["custom_labour_name"] = (frappe.db.get_value("Employee", vals["custom_labour"], "employee_name")
                                      if vals["custom_labour"] else None)
        frappe.db.set_value("Work Order Operation", d.name, vals)
    frappe.db.commit()
    return get_work_order(name)


@frappe.whitelist()
def update_stage_entry(name, idx, entered_on, notes=None):
    """Correct when a stage was entered (and its note). The time is stamped
    automatically on each move; this is for when the move was recorded late.
    Hours are re-worked for this stage and the one after it."""
    wo = frappe.get_doc("Work Order", name)
    if wo.docstatus == 2:
        frappe.throw(_("Work order {0} is cancelled").format(name))
    rows = list(wo.custom_stage_history)
    pos = next((i for i, h in enumerate(rows) if h.idx == cint(idx)), None)
    if pos is None:
        frappe.throw(_("Stage history row {0} not found").format(idx))
    when = get_datetime(str(entered_on).replace("T", " "))
    if when > now_datetime():
        frappe.throw(_("The time cannot be in the future"))
    if pos > 0 and rows[pos - 1].entered_on and when < get_datetime(rows[pos - 1].entered_on):
        frappe.throw(_("{0} cannot start before {1} ({2})").format(
            rows[pos].stage, rows[pos - 1].stage, frappe.utils.format_datetime(rows[pos - 1].entered_on, "dd MMM yyyy, HH:mm")))
    if pos < len(rows) - 1 and rows[pos + 1].entered_on and when > get_datetime(rows[pos + 1].entered_on):
        frappe.throw(_("{0} cannot start after {1} ({2})").format(
            rows[pos].stage, rows[pos + 1].stage, frappe.utils.format_datetime(rows[pos + 1].entered_on, "dd MMM yyyy, HH:mm")))
    rows[pos].entered_on = when
    if notes is not None:
        rows[pos].notes = notes
    for i in (pos, pos + 1):
        if i >= len(rows):
            continue
        h = rows[i]
        dur = round(time_diff_in_hours(h.entered_on, rows[i - 1].entered_on), 2) if i > 0 and rows[i - 1].entered_on else 0
        vals = {"entered_on": h.entered_on, "duration_hours": dur}
        if i == pos:
            vals["notes"] = h.notes
        frappe.db.set_value("Workshop Work Order Stage Log", h.name, vals)
    frappe.db.commit()
    return get_work_order(name)


# ---------------------------------------------------------------------------
# Advance the production stage (final stage submits + posts stock)
# ---------------------------------------------------------------------------


def _append_stage_log(wo, to_stage, notes):
    """Append one transition row, computing time spent in the previous stage."""
    now = now_datetime()
    prev = wo.custom_stage_history[-1] if wo.custom_stage_history else None
    duration = 0.0
    if prev and prev.entered_on:
        duration = round(time_diff_in_hours(now, prev.entered_on), 2)
    wo.append(
        "custom_stage_history",
        {
            "stage": to_stage,
            "from_stage": wo.custom_production_stage or "",
            "entered_on": now,
            "entered_by": frappe.session.user,
            "duration_hours": duration,
            "notes": notes or "",
        },
    )


@frappe.whitelist()
def advance_stage(name, notes=None, sheet_choices=None, allow_shortage=0):
    """Move the Work Order to the next production stage. Advancing into the final
    Delivery stage submits the Work Order and posts a Manufacture Stock Entry so
    the finished item lands in stock.

    A BOM with sheet rows needs `sheet_choices` ([{row, source, leftovers}], the
    Sheet material dialog) before Delivery: per row, cut from leftovers, from a
    new sheet, or leftovers first and the balance from new."""
    wo = frappe.get_doc("Work Order", name)
    cur = wo.custom_production_stage or "Material"
    idx = _stage_index(cur)
    if idx >= len(STAGES) - 1:
        frappe.throw(_("Work order is already at the final stage"))

    nxt = STAGES[idx + 1]
    if cur == STAGES[0]:
        # Production starts only when all the material is there, counting what
        # other work orders already in production will use -- unless the user
        # chose "Continue with negative stock" in the shortage pop-up. Items that
        # can never go negative (batch items) still block.
        short = _shortages(wo, reserve=True)
        if short and cint(allow_shortage):
            hard = _blocking(short, all_rows=False)
            if hard:
                frappe.throw(_("These are batch items and cannot go negative. Receive them first.")
                             + "<br><br>" + _shortage_text(hard), title=_("Material short"))
            line = _("Started short (stock will go negative): {0}").format(
                ", ".join("{0} {1} {2}".format(r["item_name"], r["short"], r["uom"] or "") for r in short))
            notes = (notes + " | " + line) if notes else line
        elif short:
            frappe.throw(_("Not enough material to start this work order (after what other work orders in "
                           "production need). Receive it first, then move it to Fabrication.")
                         + "<br><br>" + _shortage_text(short), title=_("Material short"))
    _sheet_material(wo, cur, nxt, sheet_choices)
    if cur == "QC":
        # Leaving QC is the QC sign-off: it lands on the Quality screen's list,
        # and a failed inspection holds the trailer back.
        from a3_trading_management.api.quality import ensure_passed_before_delivery

        ensure_passed_before_delivery(wo, notes)
    _append_stage_log(wo, nxt, notes)
    wo.custom_production_stage = nxt
    wo.flags.ignore_permissions = True
    wo.save(ignore_permissions=True)

    result = {"stock_entry": None, "stock_warning": None}
    if nxt == "Delivery":
        # Safety net (checked at Fabrication already): never deliver half-posted.
        # With Allow Negative Stock on, only items that cannot go negative block.
        _check_stock(wo, blocking_only=True)
        result = _complete_production(wo)

    detail = get_work_order(name)
    detail.update(result)
    return detail


def _sheet_material(wo, cur, nxt, sheet_choices):
    """Sheets are cut as the work order leaves its first stage (Material): the
    user's choice per sheet row (leftover / new sheet / both) is checked, the
    pieces are cut and kept for this work order. A work order already past
    Material when this came in is asked at Delivery instead, as before."""
    from a3_trading_management.api.sheet_cut import check_plan, cut_for_work_order, needs, plan_doc

    if not (cur == STAGES[0] or nxt == "Delivery"):
        return
    pd = plan_doc(wo.get("custom_sheet_plan"))
    if pd["cut"] or not needs(wo):
        return
    choices = check_plan(wo, sheet_choices if sheet_choices else pd["choices"])
    if cur == STAGES[0]:
        used = cut_for_work_order(wo, choices)
        wo.custom_sheet_plan = json.dumps({"choices": choices, "cut": [list(u) for u in used]})
        return
    # Late path (already past Material): try the cuts, undo, cut for real at Manufacture.
    frappe.db.savepoint("sheet_dry_run")
    try:
        cut_for_work_order(wo, choices)
    finally:
        frappe.db.rollback(save_point="sheet_dry_run")
    wo.custom_sheet_plan = json.dumps({"choices": choices, "cut": []})


def _held_by_others(wo):
    """Material other work orders in production (past Material, stock not yet
    posted) still need: {(item, warehouse): qty}."""
    held = {}
    for r in frappe.db.sql("""
            select wi.item_code, coalesce(wi.source_warehouse, w.source_warehouse) as wh,
                sum(wi.required_qty - wi.consumed_qty) as qty
            from `tabWork Order Item` wi join `tabWork Order` w on w.name = wi.parent
            where w.name != %s and w.docstatus < 2
                and w.custom_production_stage in ('Fabrication', 'Assembly', 'Paint', 'QC', 'Delivery')
                and not exists (select 1 from `tabStock Entry` se where se.work_order = w.name
                    and se.purpose = 'Manufacture' and se.docstatus = 1)
            group by wi.item_code, wh""", wo.name, as_dict=True):
        held[(r.item_code, r.wh)] = flt(r.qty)
    return held


def _trailer_deliveries(work_order):
    """Each trailer built on this work order and where it went: the submitted
    Delivery Note it left on (by its serial, or by chassis number for a note
    carrying several trailers), the customer, and the invoice raised on it.
    The production stage "Delivery" only means finished and handed to the yard."""
    out = []
    for t in frappe.get_all("Trailer Serial", filters={"work_order": work_order},
                            fields=["name", "chassis_number", "status", "current_owner", "warehouse"],
                            order_by="creation asc"):
        dn = frappe.db.sql(
            """select name, posting_date, customer_name from `tabDelivery Note`
            where docstatus = 1 and (custom_trailer_serial = %(s)s
                or (%(c)s != '' and concat(', ', ifnull(custom_chassis_number, ''), ',') like %(like)s))
            order by posting_date desc, creation desc limit 1""",
            {"s": t.name, "c": t.chassis_number or "", "like": "%, " + (t.chassis_number or "") + ",%"}, as_dict=True)
        dn = dn[0] if dn else None
        invoice = None
        if dn:
            invoice = frappe.db.sql(
                """select sii.parent from `tabSales Invoice Item` sii join `tabSales Invoice` si on si.name = sii.parent
                where si.docstatus = 1 and si.is_return = 0 and sii.delivery_note = %s limit 1""", dn.name)
            invoice = invoice[0][0] if invoice else None
        out.append({
            "serial": t.name, "chassis": t.chassis_number, "status": t.status,
            "delivered": bool(dn), "customer": (dn.customer_name if dn else None) or (t.current_owner if dn else None),
            "delivery_note": dn.name if dn else None,
            "delivery_date": frappe.utils.formatdate(dn.posting_date, "dd MMM yyyy") if dn else None,
            "invoice": invoice, "yard": None if dn else t.warehouse,
        })
    return out


def _shortages(wo, reserve=False):
    """Raw material the work order still needs but its warehouse does not have.
    With `reserve`, what other work orders in production need is set aside first.
    Checked even with Allow Negative Stock on: that setting is there so past
    shortages can show as negative stock, not to start jobs without material."""
    held = _held_by_others(wo) if reserve else {}
    out = []
    for d in wo.required_items:
        need = flt(d.required_qty) - flt(d.consumed_qty)
        if need <= 0 or not frappe.get_cached_value("Item", d.item_code, "is_stock_item"):
            continue
        wh = d.source_warehouse or wo.source_warehouse or wo.wip_warehouse
        have = flt(frappe.db.get_value("Bin", {"item_code": d.item_code, "warehouse": wh}, "actual_qty"))
        have = max(0.0, have - held.get((d.item_code, wh), 0))
        if have + 1e-6 < need:
            out.append({"item_code": d.item_code, "item_name": d.item_name or d.item_code, "warehouse": wh,
                        "uom": d.stock_uom, "required": flt(need, 3), "in_stock": flt(have, 3),
                        "short": flt(need - have, 3)})
    return out


def _shortage_text(rows):
    return "<br>".join("{0}: needs {1} {2}, {3} available in {4}".format(
        frappe.bold(r["item_name"]), r["required"], r["uom"] or "", r["in_stock"], r["warehouse"]) for r in rows)


def _blocking(rows, all_rows=None):
    """The shortages that stop a move: batch items always (ERPNext never lets a
    batch go negative); every row when Allow Negative Stock is off."""
    if all_rows is None:
        all_rows = not cint(frappe.db.get_single_value("Stock Settings", "allow_negative_stock"))
    if all_rows:
        return rows
    return [r for r in rows if frappe.get_cached_value("Item", r["item_code"], "has_batch_no")]


def _check_stock(wo, reserve=False, blocking_only=False):
    rows = _shortages(wo, reserve)
    if blocking_only:
        rows = _blocking(rows)
    if rows:
        msg = (_("Not enough material to start this work order (after what other work orders in production "
                 "need). Receive it first, then move it to Fabrication.") if reserve
               else _("Not enough material to finish this work order. Receive it first."))
        frappe.throw(msg + "<br><br>" + _shortage_text(rows), title=_("Material short"))


def _open_wo_request(wo_name):
    """The open Purchase Material Request raised for this work order, if any."""
    return frappe.db.get_value("Material Request", {
        "work_order": wo_name, "material_request_type": "Purchase", "docstatus": ["<", 2],
        "status": ["not in", ["Cancelled", "Stopped", "Received"]]}, "name")


@frappe.whitelist()
def material_check(name):
    """What the shortage pop-up shows before leaving Material: each short item
    (after what other work orders in production need), whether it is a batch
    item (cannot go negative), and any open request already holding it."""
    wo = frappe.get_doc("Work Order", name)
    from a3_trading_management.api.live_stock import _open_mr_by_item

    on_mr = _open_mr_by_item()
    rows = _shortages(wo, reserve=True)
    for r in rows:
        r["batch"] = bool(frappe.get_cached_value("Item", r["item_code"], "has_batch_no"))
        r["requested"] = on_mr.get(r["item_code"])
    return {"rows": rows, "material_request": _open_wo_request(name),
            "can_continue": bool(rows) and not any(r["batch"] for r in rows)}


@frappe.whitelist()
def request_work_order_shortfall(name):
    """One Purchase Material Request per work order for what it is short of.
    If the order already has an open one, that one is returned instead."""
    frappe.has_permission("Material Request", "create", throw=True)
    existing = _open_wo_request(name)
    if existing:
        return {"material_request": existing, "existing": True}
    wo = frappe.get_doc("Work Order", name)
    rows = _shortages(wo, reserve=True)
    if not rows:
        frappe.throw(_("Nothing is short for {0}").format(name))
    from a3_trading_management.api.live_stock import _company, _mr_warehouse

    mr = frappe.new_doc("Material Request")
    mr.material_request_type = "Purchase"
    mr.company = wo.company or _company()
    mr.transaction_date = frappe.utils.nowdate()
    mr.schedule_date = frappe.utils.nowdate()
    mr.work_order = wo.name
    mr.title = _("For {0}: {1}").format(wo.name, wo.item_name or wo.production_item)[:140]
    for r in rows:
        mr.append("items", {"item_code": r["item_code"], "qty": r["short"], "schedule_date": frappe.utils.nowdate(),
                            "warehouse": r["warehouse"] or _mr_warehouse()})
    mr.flags.ignore_mandatory = True
    mr.insert(ignore_permissions=True)
    return {"material_request": mr.name, "existing": False, "items": len(rows)}


@frappe.whitelist()
def post_production_stock(name):
    """For a work order that reached Delivery without its stock being posted
    (material was short then): post the Manufacture entry now."""
    wo = frappe.get_doc("Work Order", name)
    if wo.docstatus != 1 or (wo.custom_production_stage or "") != "Delivery":
        frappe.throw(_("Only a delivered work order can post its stock here"))
    if frappe.db.exists("Stock Entry", {"work_order": wo.name, "purpose": "Manufacture", "docstatus": 1}):
        frappe.throw(_("Stock for {0} is already posted").format(name))
    _check_stock(wo, blocking_only=True)
    result = _post_manufacture(wo)
    if result["stock_warning"]:
        frappe.throw(result["stock_warning"], title=_("Stock not posted"))
    frappe.db.commit()
    detail = get_work_order(name)
    detail.update(result)
    return detail


def _complete_production(wo):
    """Submit the Work Order and post a Manufacture Stock Entry. Stock is checked
    first (_check_stock); should the entry still fail, the whole move to Delivery
    is undone so the screen never shows a delivered order with nothing in stock."""
    wo.flags.ignore_permissions = True
    wo.submit()
    result = _post_manufacture(wo)
    if result["stock_warning"]:
        frappe.throw(_("Stock could not be posted, so the work order stays at QC: {0}").format(result["stock_warning"]),
                     title=_("Stock not posted"))
    return result


def _post_manufacture(wo):
    """Post the Manufacture Stock Entry; returns {stock_entry, stock_warning}."""

    # The portal tracks operations through the stage stepper rather than ERPNext
    # Job Cards, so mark every operation complete for the produced qty; otherwise
    # the Manufacture Stock Entry refuses to post (check_if_operations_completed).
    for op in wo.operations:
        frappe.db.set_value(
            "Work Order Operation",
            op.name,
            {"completed_qty": wo.qty, "status": "Completed"},
            update_modified=False,
        )

    stock_entry = None
    warning = None
    try:
        frappe.db.savepoint("mfg_manufacture")
        se = frappe.get_doc(make_stock_entry(wo.name, "Manufacture"))
        if wo.get("custom_sheet_plan"):
            # Sheets: consume exactly the pieces cut for this work order (cut now
            # if it was still waiting for them).
            from a3_trading_management.api.sheet_cut import apply_to_manufacture_entry, cut_for_work_order, plan_doc

            pd = plan_doc(wo.custom_sheet_plan)
            apply_to_manufacture_entry(se, pd["cut"] or cut_for_work_order(wo, pd["choices"]))
        # make_stock_entry sets `purpose` but not `stock_entry_type`, which is the
        # mandatory field on Stock Entry — without it the entry never posts and the
        # work order silently completes with nothing in the warehouse.
        if not se.get("stock_entry_type"):
            se.stock_entry_type = "Manufacture"
        se.flags.ignore_permissions = True
        se.insert(ignore_permissions=True)
        se.submit()
        stock_entry = se.name
    except Exception as exc:
        frappe.db.rollback(save_point="mfg_manufacture")
        warning = str(exc) or _("Stock entry could not be posted; check raw material stock.")
        frappe.log_error(frappe.get_traceback(), "Manufacturing: Manufacture Stock Entry failed")

    return {"stock_entry": stock_entry, "stock_warning": warning}


def stock_unit_rates(doc, method=None):
    """Work Order validate: ERPNext copies a BOM line's rate per BOM unit (e.g. per
    Meter) onto the work order, whose quantity is in the stock unit (Centimeter),
    so 250 cm of pipe at 10 per Meter showed as 2,500. A row still holding the BOM
    rate gets it per stock unit; a rate typed on the work order is left alone."""
    if not doc.bom_no:
        return
    bom_rates = {}
    for b in frappe.get_all("BOM Item", filters={"parent": doc.bom_no, "parenttype": "BOM"},
                            fields=["item_code", "rate", "conversion_factor"]):
        if flt(b.conversion_factor) and flt(b.conversion_factor) != 1:
            bom_rates.setdefault(b.item_code, (flt(b.rate), flt(b.conversion_factor)))
    for d in doc.required_items:
        if d.item_code in bom_rates:
            rate, cf = bom_rates[d.item_code]
            if abs(flt(d.rate) - rate) < 1e-9:
                d.rate = rate / cf
                d.amount = flt(d.rate) * flt(d.required_qty)
