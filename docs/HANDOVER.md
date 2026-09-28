# A3 Trading Management — Handover Notes

For the Redlines team. Covers what was built, what you must configure before go‑live,
how the app is put together, and what is deliberately not included.

---

## 1. What A3 Trading Management is

A trailer trading business on ERPNext: buying material, building trailers, stocking
and selling them — each identified by its chassis number — with a portal for the
day‑to‑day work. Trading and manufacturing only: there is no workshop‑service side.

The single most important idea: **a trailer is identified by its chassis number, not
counted as a quantity.** Stock of trailers is a list of identified units.

**One app.** `a3_trading_management` is self‑contained: its own record types (Trailer
Serial, Chassis Numbering Rule, the work‑order stage history), its own portal
(templates, stylesheet, pages) and its own APIs. It depends on Frappe and ERPNext only.

Until 23 Sep 2026 it sat on top of two shared apps, `garagedesk` and
`a3_workshop_frontend`. Those were uninstalled and removed from the bench on that date;
the portal and the manufacturing screens were forked into this app, and the rest was
dropped. On the same day the client confirmed the product is **trading and
manufacturing only**, so the workshop‑service screens that had come with the fork
(job cards, front office, daily planner, telecalling, complaints, technician board)
and their record types were removed as well (`setup/trim_to_trading.py`). Nothing here
pulls from or pushes to those repositories any more.

---

## 2. Before you go live — required configuration

These are not optional. The system will refuse work, or behave oddly, until they are done.

### 2.1 Chassis numbering rule — **blocks trailer work orders**

Open **Chassis Numbering Rule** (single doctype) and set the format A3 actually uses:
prefix, whether the year is included, separator, sequence length, starting number, and
check digit (None / Mod 10 Luhn / Mod 11). The form previews the next number.

Then tick **Format Confirmed by Redlines**.

Until that box is ticked, a work order for a trailer is refused with a clear message.
This is deliberate: the chassis number is embossed into metal, and a guessed format
cannot be taken back. Nothing in the build assumes a format.

### 2.2 Mark your trailer items

A chassis number is only issued for an Item ticked **Is a Trailer** (on the Item form).
Without it, a work order is an ordinary work order — which is what you want for parts.

### 2.3 Supplier compliance

Each Supplier carries trade licence, VAT certificate and establishment expiry dates plus
a compliance status. A purchase order to a supplier whose papers have lapsed is refused.
Load the real dates before buying starts, or ordering will stop.

### 2.4 ERPNext masters

This site was built from a bare install, so the following were created by hand and should
be reviewed against how Redlines actually runs: Company (**A3 Trading**, AED), warehouses,
fiscal years, price lists (Standard Buying / Standard Selling), stock entry types, UOMs,
item groups and the company tax ID. The chart of accounts is the ERPNext default.

---

## 3. What was built

### Phase 1 — base system and removals
* The vehicle register, drivers and handover, and vehicle intake/damage capture do not
  exist in this app. The six vehicle portal screens are gone; their old addresses redirect
  to the portal home.
* Modules: Vehicle Inspection is **Unit Inspection**, Garage CRM is **Trading CRM**.
* Phase 1 tasks 4–6 (repoint the inspection doctypes to the trailer serial, related
  report rework, roles/branches/permissions review) are **still open**.

### Phase 2 — serial and chassis control
* **Trailer Serial** — one record per physical trailer: chassis number, trailer type,
  originating work order, production stage, warehouse, owner, warranty, embossing.
* **Chassis Numbering Rule** — the configurable format described above.
* A work order for N trailers issues N chassis numbers and N serials, reserved to it.
  The chassis number is frozen from then on and cannot be edited.
* The serial follows the six production stages and enters the warehouse on completion.
* **Serial & Chassis Register** and **Chassis Plate Print** screens.
* Warranty start and expiry are carried on the serial.

### Phases 3 and 4 — buying, selling and accounts
Ten portal screens: Suppliers, Purchase Requisitions, Purchase Orders, Goods Receipt,
Purchase Invoices, Supplier Payments, Sales Orders, Delivery & Invoice, Payments &
Receipts, Journal Entries.

These are **views onto the standard ledger**. No parallel books are kept: approving a
purchase invoice submits the real Purchase Invoice, posting a journal submits the real
Journal Entry. Anything you see here is visible in the Desk too.

### Phase 5 — the four additional points
1. Service packages, service menus and the labour/part/sublet kits do not exist (and,
   since the trim, neither do job cards and estimates); order lines pick **stock items**.
2. Manufacturing sits with the trading navigation.
3. Sales invoice **delete** action, permission‑guarded, with a double confirm.
4. Live Stock shows total quantity per item with where it is held, and trailers as
   identified chassis numbers.

---

## 4. Things worth knowing

* **The approval hold on a purchase invoice is ours, not ERPNext's.** ERPNext's own
  `on_hold` can only be set *after* submitting — by then the bill has already posted.
  A separate "Held for Approval" flag gates the draft before it reaches the ledger.
* **A trailer only counts as in stock once the Manufacture entry posts.** Advancing the
  stage alone does not put stock in the warehouse.
* **The production stages live on the Work Order** (`custom_production_stage`, with the
  stage history table) and are advanced from the Manufacturing screen. The Trailer Serial
  mirrors the stage; it does not own it.
* **The `Vehicle Inspection` doctype still carries that name.** Only the module was
  renamed. Renaming the doctype is a data migration and was left alone deliberately.
* **Cancelling a work order** marks its serials Scrapped but keeps the chassis numbers —
  reissuing one would put the same number on two trailers.
* **Items are numbered from a series** while Item Code stays a typeable field (the
  `Item.before_naming` hook). Accept the suggested code or type your own.

---

## 5. How the app is organised

```
a3_trading_management/
  a3_trading_management/   Trailer Serial, Chassis Numbering Rule,
                           Workshop Work Order Stage Log            (module: A3 Trading Management)
  platform/                error log                                 (Platform)
  api/                     buying, selling, serial_register, stock_board, manufacturing,
                           live_stock, dashboard, customers, session, signature, error_log
  integrations/item.py     items numbered from a series, Item Code still typeable
  serial_control.py        chassis issue on Work Order events; stage sync; Manufacture entry
  fixtures/                custom fields (Supplier compliance, Work Order stage, Customer CRM,
                           Employee signature), property setters, one client script,
                           the Sales Order print format, two roles
  www/a3-workshop/         the portal: home, customers, manufacturing, serial register,
                           chassis plates, suppliers, transactions + 9 screens, live stock,
                           the work-order traveler print
  templates/               the portal shell, macros, print base;  public/scss/  the stylesheet
  setup/                   install (custom fields, roles), trim_to_trading (one-off, section 5.2)
  tests/e2e_cycle.py       the end-to-end cycle test
```

### 5.1 What was forked, and what was left behind

The fork (23 Sep 2026) took, from the workshop platform, the portal shell and the
manufacturing screen with the code behind them, the supplier compliance and work‑order
stage fields, and the item numbering hook. Everything else — job cards, appointments,
estimates, telecalling, service reminders, inspection, repair warranty, franchise,
e‑invoicing, parts operations, compliance registers, collaboration — was left behind
or removed in the same‑day trim. Source trees and history of the old apps:
`~/bench/a3_trading/backups/*.bundle` (`git init && git bundle unbundle <bundle>`
restores them, including the pre‑fork trading branches).

### 5.2 The one‑off migrations (already done on this site; for the record)

`setup/trim_to_trading.py` removed the workshop‑service modules from the database
(record types and tables, reports, workspaces, notifications, workflows, the custom
fields that only served them, and unused roles). It ran once, with the module folders
still on disk, and the folders were deleted afterwards. Not needed on a fresh install.

### 5.3 Fresh install

```bash
bench get-app <this repo>
bench --site <site> install-app a3_trading_management   # after erpnext
bench build --app a3_trading_management
```
`after_install` creates the roles and settings singles, then the trading custom fields.

---

## 5a. The user guide

`docs/USER-GUIDE.html` is the plain‑language walkthrough for the client's staff: one
trailer's road from buying material to delivery, screen by screen, with
the exact button names, the refusals and their reasons, a status glossary and an
17‑point acceptance checklist that mirrors the automated cycle test below.
`docs/TEST-SCRIPT.html` is the manual test script: 24 test cases with exact clicks,
the data to enter (the records on this site) and the expected result of each step,
with pass/fail tracking. Both open in a browser and need no server.

---

## 6. Running the end‑to‑end test

A scripted walk‑through of the whole cycle — requisition → order → receipt → bill →
payment → work order → chassis issue → build → stock → sales order → delivery → invoice
→ warranty:

```bash
bench --site <site> execute a3_trading_management.tests.e2e_cycle.run
```

It posts real documents, so run it on a test site. It prints each step and fails loudly
if any link in the chain breaks.

---

## 7. Not included

Per section 09 of the scope: historical data load, new third‑party integrations, a native
mobile app, and payroll/HR.

Also still open at handover:
* **Phase 1 tasks 4–6** as originally scoped are mostly moot after the trim (the
  inspection and job‑card doctypes no longer exist); what remains of them is the
  roles and permissions review.
* Deployment itself (web server, workers, backups, SSL) has not been done — the build has
  only been exercised on the development bench.
