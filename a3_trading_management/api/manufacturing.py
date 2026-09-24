"""Manufacturing facade (a3_trading_management.api.manufacturing.*).

Backs the Workshop Manufacturing page (a3_trading_management
www/a3-workshop/manufacturing.html and its mirror in a3_workshop_management).

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
    """Items that can be manufactured -- i.e. that already have at least one
    active BOM. Used by the 'Item to be manufactured' autocomplete."""
    txt = "%{0}%".format((search or "").strip())
    rows = frappe.db.sql(
        """
        SELECT DISTINCT b.item AS name, i.item_name, i.stock_uom, i.description
        FROM `tabBOM` b
        INNER JOIN `tabItem` i ON i.name = b.item
        WHERE b.is_active = 1
          AND (b.item LIKE %(txt)s OR i.item_name LIKE %(txt)s)
        ORDER BY i.item_name
        LIMIT %(limit)s
        """,
        {"txt": txt, "limit": cint(limit) or 20},
        as_dict=True,
    )
    for r in rows:
        r["default_bom"] = _default_bom_for_item(r["name"])
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

    return {
        "cards": [
            {"key": "open", "label": "Open Work Orders", "value": open_wo},
            {"key": "prod", "label": "In Production", "value": in_prod},
            {"key": "ready", "label": "Ready for Delivery", "value": ready},
            {"key": "delivered", "label": "Delivered (MTD)", "value": delivered_mtd},
            {"key": "wip", "label": "WIP Value", "value": _money(wip_value, currency)},
            {"key": "ontime", "label": "On-time %", "value": "{0}%".format(on_time_pct)},
        ]
    }


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

    operations = [
        {
            "idx": d.idx,
            "operation": d.operation,
            "workstation": d.workstation,
            "time_in_mins": flt(d.time_in_mins),
            "hours": round(flt(d.time_in_mins) / 60.0, 2),
            "hour_rate": flt(d.hour_rate),
            "hour_rate_fmt": _money(d.hour_rate, currency),
            "operating_cost": flt(d.planned_operating_cost),
            "operating_cost_fmt": _money(d.planned_operating_cost, currency),
        }
        for d in wo.operations
    ]

    rm_cost = sum(flt(d.amount) for d in wo.required_items)
    op_cost = flt(wo.total_operating_cost)
    add_cost = flt(wo.additional_operating_cost)
    total = rm_cost + op_cost + add_cost
    per_unit = total / flt(wo.qty) if flt(wo.qty) else 0

    history = [
        {
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

    return {
        "name": wo.name,
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
        "costing": {
            "raw_material": _money(rm_cost, currency),
            "operating": _money(op_cost, currency),
            "additional": _money(add_cost, currency),
            "total": _money(total, currency),
            "per_unit": _money(per_unit, currency),
        },
        "stock_entry": stock_entry,
        "currency": currency,
    }


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

    if required_items is not None:
        rows = required_items if isinstance(required_items, list) else json.loads(required_items)
        wo.set("required_items", [])
        for r in rows:
            item_code = (r.get("item_code") or "").strip()
            if not item_code:
                continue
            qty = flt(r.get("qty"))
            rate = flt(r.get("rate"))
            wo.append(
                "required_items",
                {
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

    if operations is not None:
        rows = operations if isinstance(operations, list) else json.loads(operations)
        wo.set("operations", [])
        for r in rows:
            op = _ensure_operation(r.get("operation"))
            if not op:
                continue
            wo.append(
                "operations",
                {
                    "operation": op,
                    "workstation": _ensure_workstation(r.get("workstation")),
                    "time_in_mins": flt(r.get("time_in_mins")),
                    "hour_rate": flt(r.get("hour_rate")),
                },
            )

    wo.flags.ignore_permissions = True
    wo.save(ignore_permissions=True)
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
def advance_stage(name, notes=None):
    """Move the Work Order to the next production stage. Advancing into the final
    Delivery stage submits the Work Order and posts a Manufacture Stock Entry so
    the finished item lands in stock."""
    wo = frappe.get_doc("Work Order", name)
    cur = wo.custom_production_stage or "Material"
    idx = _stage_index(cur)
    if idx >= len(STAGES) - 1:
        frappe.throw(_("Work order is already at the final stage"))

    nxt = STAGES[idx + 1]
    _append_stage_log(wo, nxt, notes)
    wo.custom_production_stage = nxt
    wo.flags.ignore_permissions = True
    wo.save(ignore_permissions=True)

    result = {"stock_entry": None, "stock_warning": None}
    if nxt == "Delivery":
        result = _complete_production(wo)

    detail = get_work_order(name)
    detail.update(result)
    return detail


def _complete_production(wo):
    """Submit the Work Order and post a Manufacture Stock Entry. Stock posting is
    best-effort: if raw material isn't on hand the entry is rolled back (via a
    savepoint) and a warning is returned, but the stage still reads Delivery."""
    wo.flags.ignore_permissions = True
    wo.submit()

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
