"""Build the client demo site end to end: company, settings, users and four months of trading.

On a site made for the demo -- never the live books:

    bench new-site a3_demo --install-app erpnext --install-app a3_trading_management
    bench --site a3_demo execute a3_trading_management.demo.build.run

What it loads, in order:

1. Setup wizard: Redlines Trading on the U.A.E chart, AED, Asia/Dubai, FY 2026.
2. The settings production was given by hand: VAT 5% as the default sales and
   purchase template, zero-rated / exempt item templates that zero the 5%, the
   company's Dubai address (VAT 201 reads its emirate), UAE VAT Settings, the
   chassis numbering rule, manufacturing yards, a letter head, the role profiles.
3. One login per department (DEMO_PASSWORD), including the three people the
   invoice-deletion approval chain needs.
4. Masters (demo.seed) dated June, plus production labour and customer addresses.
5. July to September: three closed months of buy -> build -> QC -> sell -> collect,
   with one August invoice left unpaid and one September invoice half paid, so
   receivables ageing and the VAT return have something to show.
6. 1 to 7 October: demo.seed's open picture (drafts, held bills, work in progress,
   reservations, part payments), then demo.seed_quality.
7. Invoice deletion requests in every state of the approval chain.

Documents are posted on their own dates by moving the clock (see `clock`), so
creation times, stage logs and ledger dates agree. Runs once; a second run stops
unless `force=1`, and refuses any site whose name does not contain "demo".
"""

import random
import sys
from contextlib import contextmanager
from datetime import datetime, time, timedelta

import frappe
import frappe.utils.data
from frappe.utils import add_days, flt, getdate

from a3_trading_management.api import buying, manufacturing, quality, selling, serial_register
from a3_trading_management.demo import seed, seed_quality

COMPANY = "Redlines Trading"
ABBR = "RT"
DEMO_PASSWORD = "Demo@2026"
MAIL = "redlines.demo"

COMPANY_DETAILS = {
	"tax_id": "105503411800003",
	"phone_no": "04 355 8595",
	"email": "info@redlines.ae",
	"website": "https://redlines.ae",
}
ADDRESS = {
	"address_line1": "G4, Bank Street Building",
	"address_line2": "BurJuman",
	"city": "Dubai",
	"pincode": "27969",
	"phone": "04 355 8595",
	"email_id": "info@redlines.ae",
}

# Role profiles as production has them (Super Admin is kept by setup.deletion_workflow).
ROLE_PROFILES = {
	"Purchase": ["Purchase Manager", "Purchase User", "Stock User", "Item Manager"],
	"Sales": ["Sales Manager", "Sales User", "Stock User"],
	"Accounts": ["Accounts Manager", "Accounts User"],
	"Manufacturing": ["Manufacturing Manager", "Manufacturing User", "Stock User", "Quality Manager"],
	"Inventory": ["Item Manager", "Stock Manager", "Stock User"],
}

# login: (full name, role profile, extra roles)
USERS = {
	"owner": ("Khalid Al Rashed", "Super Admin", []),
	"purchase": ("Priya Menon", "Purchase", []),
	"sales": ("Omar Haddad", "Sales", []),
	"accounts": ("Fatima Noor", "Accounts", []),
	"production": ("Rajesh Kumar", "Manufacturing", []),
	"stores": ("Imran Sheikh", "Inventory", []),
	"operator": ("Anil Thomas", None, ["Operator", "Accounts User", "Sales User", "Purchase User"]),
	"finance": ("Sara Al Mazrouei", None, ["Finance Manager", "Accounts User"]),
}

# The floor: who does each operation (copied onto every work order from the BOM).
LABOUR = {
	"Cutting": ("Suresh Pillai", "Cutter"),
	"Welding & Fabrication": ("Mohammed Aslam", "Welder"),
	"Assembly": ("Ramesh Yadav", "Fitter"),
	"Painting": ("Joseph Mathew", "Painter"),
	"Quality Inspection": ("Abdul Kareem", "QC Inspector"),
}

CUSTOMER_ADDRESSES = {
	"gulf_horizon": ("Warehouse 14, Al Quoz Industrial Area 3", "Dubai"),
	"desert_line": ("Plot 22, Industrial Area 13", "Sharjah"),
	"al_noor": ("Mussafah M-26, Plot 9", "Abu Dhabi"),
	"falcon": ("Office 1204, Business Bay Tower", "Dubai"),
	"blue_dune": ("Al Jazeera Al Hamra Industrial Zone", "Ras Al Khaimah"),
	"crescent": ("Khalifa Port Logistics Park, Unit 7", "Abu Dhabi"),
	"sandstone": ("Al Sajaa Industrial, Shed 31", "Sharjah"),
	"oasis": ("Sohar Free Zone, Block C", "Sohar"),
	"rashid": ("Villa 18, Street 9, Al Barsha 2", "Dubai"),
	"swift": ("Second Industrial City, Phase 2", "Dammam"),
}

# Three closed months. Orders: (customer key, trailer, qty, settlement).
# settlement: "full" paid that month, "half" half paid, "none" left owing.
HISTORY = [
	("2026-07-01", [("gulf_horizon", "TRL-LOWBED-3AX", 2, "full"),
	                ("crescent", "TRL-SKELETAL-40", 2, "full"),
	                ("falcon", "TRL-CURTAIN-3AX", 1, "full")]),
	("2026-08-01", [("desert_line", "TRL-CURTAIN-3AX", 2, "full"),
	                ("al_noor", "TRL-LOWBED-3AX", 1, "full"),
	                ("oasis", "TRL-SKELETAL-40", 2, "none")]),
	("2026-09-01", [("blue_dune", "TRL-TIPPER-2AX", 2, "full"),
	                ("sandstone", "TRL-TIPPER-2AX", 1, "half"),
	                ("crescent", "TRL-SKELETAL-40", 3, "full"),
	                ("gulf_horizon", "TRL-LOWBED-3AX", 1, "full")]),
]

# Work orders in the history that failed final QC once: (month index, order index) -> failing readings.
FAILED_FIRST = {
	(0, 1): ({"Paint film thickness (microns)": "104"},
	         "Low paint build on the rear bumper and mudguard brackets. Back to the paint booth for a third coat."),
	(2, 2): ({"Air brake leak-down (psi/min)": "4.6"},
	         "Air leak at the trailer control valve. Valve re-seated and lines re-tested."),
}

OCTOBER = "2026-10-01"
BANK = object()  # _journal: the company's default bank account


# ---------------------------------------------------------------------------
# The clock
# ---------------------------------------------------------------------------
#
# ERPNext stamps posting dates, creation times and stage logs from
# frappe.utils.now_datetime(). Modules that imported it by name hold their own
# reference, so every loaded module's copy is swapped for one that adds an offset;
# the swap stays in place for the whole run (modules imported mid-run pick up the
# shifted one) and the offset goes back to zero at the end.

_REAL_NOW = frappe.utils.data.now_datetime
_offset = timedelta(0)


def _shifted_now():
	return _REAL_NOW() + _offset


def _install_clock():
	for mod in list(sys.modules.values()):
		try:
			if getattr(mod, "now_datetime", None) is _REAL_NOW:
				mod.now_datetime = _shifted_now
		except Exception:
			pass


def set_clock(day, hour=9, minute=0):
	global _offset
	target = datetime.combine(getdate(day), time(hour, minute))
	_offset = target - _REAL_NOW()


def tick(hours):
	global _offset
	_offset += timedelta(hours=hours)


def reset_clock():
	global _offset
	_offset = timedelta(0)


@contextmanager
def clock(day, hour=9):
	set_clock(day, hour)
	try:
		yield
	finally:
		reset_clock()


# ---------------------------------------------------------------------------

def _invoice_delivery(delivery_note):
	"""selling.invoice_delivery, with the due date held on or after the posting date.

	Under the shifted clock ERPNext can carry a due date from before the invoice
	date onto the invoice and then refuse it; the invoice is otherwise exactly
	what the portal makes."""
	from erpnext.stock.doctype.delivery_note.delivery_note import make_sales_invoice

	from a3_trading_management.api._common import apply_taxes

	si = make_sales_invoice(delivery_note)
	carried = frappe.db.get_value("Delivery Note", delivery_note,
	                              ["custom_trailer_serial", "custom_chassis_number"], as_dict=True) or {}
	if carried.get("custom_trailer_serial") and frappe.get_meta("Sales Invoice").has_field("custom_trailer_serial"):
		si.custom_trailer_serial = carried["custom_trailer_serial"]
		si.custom_chassis_number = carried.get("custom_chassis_number")
	si.disable_rounded_total = 1
	apply_taxes(si)
	si.posting_date = frappe.utils.nowdate()
	si.set_missing_values()
	posting = getdate(si.posting_date)
	if si.due_date and getdate(si.due_date) < posting:
		print(f"    (due date {si.due_date} before {posting} on the invoice for {delivery_note}; set to {posting})")
		si.due_date = posting
	for row in si.get("payment_schedule") or []:
		if row.due_date and getdate(row.due_date) < posting:
			row.due_date = posting
	si.flags.ignore_permissions = True
	si.insert(ignore_permissions=True)
	frappe.db.commit()
	return {"name": si.name}


def run(force=0, site_ok=0):
	"""`site_ok=1` allows a site whose name lacks "demo" (back it up first)."""
	site = frappe.local.site or ""
	if "demo" not in site and not int(site_ok):
		frappe.throw(f"demo.build posts four months of documents; run it on a demo site, not {site}.")
	frappe.set_user("Administrator")
	_install_clock()
	real_invoice_delivery = selling.invoice_delivery
	selling.invoice_delivery = _invoice_delivery
	try:
		print("Company")
		setup_company()
		print("\nSettings")
		setup_settings()
		print("\nUsers")
		setup_users()

		if frappe.db.exists("Sales Order", {"company": COMPANY}) and not int(force):
			print("\nTrading was loaded before (pass force=1 to load another set). Done.")
			return

		ctx = _ctx()
		print("\nMasters (dated June)")
		with clock("2026-06-20"):
			seed._masters(ctx)
			_labour(ctx)
			_customer_addresses()
		# Again on today's date: supplier papers expire relative to today.
		seed._masters(ctx)
		with clock("2026-06-25"):
			_capital(ctx)
			print("\nChecklists")
			seed_quality.trailer_checklist()
			frappe.db.commit()

		rng = random.Random(2026)
		for month_index, (start, orders) in enumerate(HISTORY):
			print(f"\n{getdate(start).strftime('%B %Y')}")
			_history_month(ctx, rng, month_index, start, orders)

		print("\nOctober")
		_october(ctx)

		reset_clock()
		print("\nQuality")
		seed_quality.run()
		print("\nInvoice deletion requests")
		_deletion_requests(ctx)
		_finish()
	finally:
		reset_clock()
		selling.invoice_delivery = real_invoice_delivery
	print("\nDemo site ready. Sign in as Administrator or any of: "
	      + ", ".join(f"{k}@{MAIL}" for k in USERS) + f"  (password {DEMO_PASSWORD})")


def _ctx():
	return frappe._dict(
		company=COMPANY,
		abbr=ABBR,
		stores=f"Stores - {ABBR}",
		fg=f"Finished Goods - {ABBR}",
		cost_center=frappe.db.get_value("Company", COMPANY, "cost_center"),
	)


# ---------------------------------------------------------------------------
# 1-3. Company, settings, users
# ---------------------------------------------------------------------------

def setup_company():
	if not frappe.is_setup_complete():
		from frappe.desk.page.setup_wizard.setup_wizard import setup_complete

		setup_complete({
			"language": "English",
			"country": "United Arab Emirates",
			"timezone": "Asia/Dubai",
			"currency": "AED",
			"company_name": COMPANY,
			"company_abbr": ABBR,
			"chart_of_accounts": "U.A.E - Chart of Accounts",
			"fy_start_date": "2026-01-01",
			"fy_end_date": "2026-12-31",
			"setup_demo": 0,
		})
		frappe.db.commit()
		frappe.clear_cache()
	if not frappe.db.exists("Company", COMPANY):
		frappe.throw(f"Setup did not create {COMPANY}")

	# Only fill what is blank: a site restored from production keeps its own values.
	current = frappe.db.get_value("Company", COMPANY, list(COMPANY_DETAILS) + [
		"default_cash_account", "default_bank_account"], as_dict=True)
	values = {k: v for k, v in COMPANY_DETAILS.items() if not current.get(k)}
	for field, account in (("default_cash_account", "Main Safe"),
	                       ("default_bank_account", "Banks Current Accounts")):
		name = frappe.db.get_value("Account", {"company": COMPANY, "account_name": account, "is_group": 0})
		if name and not current.get(field):
			values[field] = name
	if values:
		frappe.db.set_value("Company", COMPANY, values)
	frappe.db.set_default("company", COMPANY)

	if not frappe.db.exists("Address", {"is_your_company_address": 1}):
		doc = frappe.get_doc(dict(ADDRESS, doctype="Address", address_title=COMPANY, address_type="Billing",
		                          country="United Arab Emirates", is_your_company_address=1,
		                          links=[{"link_doctype": "Company", "link_name": COMPANY}]))
		if doc.meta.has_field("emirate"):
			doc.emirate = "Dubai"
		doc.insert(ignore_permissions=True)
	print(f"  {COMPANY} ({ABBR}), U.A.E chart, FY 2026, address in Dubai")
	frappe.db.commit()


def setup_settings():
	frappe.db.set_single_value("System Settings", {
		"date_format": "dd-mm-yyyy", "number_format": "#,###.##", "float_precision": "3",
		"time_zone": "Asia/Dubai",
	})
	# The Manufacturing screen takes raw material from Stores (no WIP transfer step).
	frappe.db.set_single_value("Manufacturing Settings", {
		"default_wip_warehouse": f"Stores - {ABBR}", "default_fg_warehouse": f"Finished Goods - {ABBR}",
	})
	frappe.db.set_single_value("Stock Settings", {"default_warehouse": f"Stores - {ABBR}",
	                                              "valuation_method": "FIFO"})

	rule = frappe.get_single("Chassis Numbering Rule")
	if not rule.confirmed_by_redlines:
		rule.update({"prefix": "RLT", "include_year": 1, "year_format": "YY", "separator": "-",
		             "sequence_length": 5, "next_number": 1, "check_digit_method": "None",
		             "confirmed_by_redlines": 1})
		rule.save(ignore_permissions=True)
	print(f"  chassis numbers {frappe.get_single('Chassis Numbering Rule').format_number(1)}, ...")

	# VAT: 5% by default on both sides.
	for doctype in ("Sales Taxes and Charges Template", "Purchase Taxes and Charges Template"):
		name = f"UAE VAT 5% - {ABBR}"
		if frappe.db.exists(doctype, name):
			frappe.db.set_value(doctype, {"company": COMPANY, "is_default": 1}, "is_default", 0)
			frappe.db.set_value(doctype, name, "is_default", 1)
	# A zero-rated or exempt item must zero the 5% too, or the default template still charges it.
	vat5 = f"VAT 5% - {ABBR}"
	for template in (f"UAE VAT Zero - {ABBR}", f"UAE VAT Exempted - {ABBR}"):
		if frappe.db.exists("Item Tax Template", template) and frappe.db.exists("Account", vat5):
			doc = frappe.get_doc("Item Tax Template", template)
			if not any(t.tax_type == vat5 for t in doc.taxes):
				doc.append("taxes", {"tax_type": vat5, "tax_rate": 0})
				doc.save(ignore_permissions=True)
	if not frappe.db.exists("UAE VAT Settings", COMPANY):
		accounts = [a for a in (vat5, f"VAT Zero - {ABBR}", f"VAT Exempted - {ABBR}") if frappe.db.exists("Account", a)]
		frappe.get_doc({"doctype": "UAE VAT Settings", "company": COMPANY,
		                "uae_vat_accounts": [{"account": a} for a in accounts]}).insert(ignore_permissions=True)
	print("  VAT 5% default, zero-rated and exempt templates, UAE VAT Settings")

	if not frappe.db.exists("Letter Head", COMPANY):
		frappe.get_doc({
			"doctype": "Letter Head", "letter_head_name": COMPANY, "is_default": 1, "source": "HTML",
			"content": (
				'<div style="display:flex;justify-content:space-between;align-items:flex-end;'
				'border-bottom:3px solid #c62828;padding-bottom:8px">'
				f'<div><div style="font-size:22px;font-weight:700;color:#c62828">{COMPANY.upper()}</div>'
				'<div style="font-size:11px;color:#555">Trailer manufacturing and trading</div></div>'
				f'<div style="text-align:right;font-size:10px;color:#555">{ADDRESS["address_line1"]}, '
				f'{ADDRESS["address_line2"]}, Dubai, UAE<br>Tel {ADDRESS["phone"]} &middot; {ADDRESS["email_id"]}'
				f'<br>TRN {COMPANY_DETAILS["tax_id"]}</div></div>'
			),
		}).insert(ignore_permissions=True)
	print("  letter head")

	for profile, roles in ROLE_PROFILES.items():
		if frappe.db.exists("Role Profile", profile):
			continue
		frappe.get_doc({"doctype": "Role Profile", "role_profile": profile,
		                "roles": [{"role": r} for r in roles if frappe.db.exists("Role", r)]}
		               ).insert(ignore_permissions=True)
	print(f"  role profiles: {', '.join(ROLE_PROFILES)}, Super Admin")

	if not frappe.db.exists("Item Group", "Trailers"):
		frappe.get_doc({"doctype": "Item Group", "item_group_name": "Trailers",
		                "parent_item_group": "All Item Groups", "is_group": 0}).insert(ignore_permissions=True)
	frappe.db.commit()


def setup_users():
	for login, (full_name, profile, roles) in USERS.items():
		email = f"{login}@{MAIL}"
		if frappe.db.exists("User", email):
			continue
		first, last = full_name.split(" ", 1)
		user = frappe.get_doc({
			"doctype": "User", "email": email, "first_name": first, "last_name": last,
			"user_type": "System User", "send_welcome_email": 0, "time_zone": "Asia/Dubai",
		})
		if profile:
			user.role_profile_name = profile
		user.insert(ignore_permissions=True)
		if roles:
			user.add_roles(*roles)
		from frappe.utils.password import update_password

		update_password(email, DEMO_PASSWORD)
		print(f"  {email:24} {profile or ', '.join(roles)}")
	frappe.db.commit()


# ---------------------------------------------------------------------------
# 4. Labour and addresses
# ---------------------------------------------------------------------------

def _labour(ctx):
	by_operation = {}
	for operation, (name, designation) in LABOUR.items():
		by_operation[operation] = manufacturing.create_labour(name, designation)["name"]
		frappe.db.set_value("Employee", by_operation[operation], "date_of_joining", "2024-03-01")
	for row in frappe.get_all("BOM Operation", filters={"parenttype": "BOM"}, fields=["name", "operation"]):
		if row.operation in by_operation:
			frappe.db.set_value("BOM Operation", row.name, "custom_labour", by_operation[row.operation],
			                    update_modified=False)
	print(f"  {len(LABOUR)} production staff on the parts lists' operations")


def _customer_addresses():
	for key, (line1, city) in CUSTOMER_ADDRESSES.items():
		name, territory = seed.CUSTOMERS[key][0], seed.CUSTOMERS[key][2]
		customer = frappe.db.get_value("Customer", {"customer_name": name}, "name")
		if not customer or frappe.db.exists("Dynamic Link", {"link_doctype": "Customer", "link_name": customer,
		                                                     "parenttype": "Address"}):
			continue
		country = territory if territory in ("Oman", "Saudi Arabia") else "United Arab Emirates"
		doc = frappe.get_doc({
			"doctype": "Address", "address_title": name, "address_type": "Billing",
			"address_line1": line1, "city": city, "country": country,
			"is_primary_address": 1, "links": [{"link_doctype": "Customer", "link_name": customer}],
		})
		if country == "United Arab Emirates" and doc.meta.has_field("emirate"):
			doc.emirate = city
		doc.insert(ignore_permissions=True)
	print(f"  billing addresses for {len(CUSTOMER_ADDRESSES)} customers")


# ---------------------------------------------------------------------------
# 5. History: three closed months
# ---------------------------------------------------------------------------

def _customer(key):
	return frappe.db.get_value("Customer", {"customer_name": seed.CUSTOMERS[key][0]}, "name")


def _supplier(key):
	return frappe.db.get_value("Supplier", {"supplier_name": seed.SUPPLIERS[key][0]}, "name")


def _history_month(ctx, rng, month_index, start, orders):
	d = lambda n: add_days(start, n)  # noqa: E731

	# Rent on the 1st.
	with clock(d(0), 10):
		_journal(ctx, [("Office Rent", 18000, 0), (None, 0, 18000)], f"Workshop and office rent, {_month(start)}")

	# Buy the month's material: ordered on the 2nd, received on the 5th, billed on the 6th.
	need = {}
	for _key, item, qty, _rule in orders:
		for code, per in seed.TRAILERS[item][2].items():
			need[code] = need.get(code, 0) + per * qty
	bills = []
	by_supplier = {}
	for code, qty in need.items():
		by_supplier.setdefault(seed.RAW_ITEMS[code][4], []).append(
			{"item_code": code, "qty": qty, "rate": seed.RAW_ITEMS[code][3], "warehouse": ctx.stores})
	for supplier_key, lines in by_supplier.items():
		with clock(d(1), 11):
			po = buying.create_purchase_order(_supplier(supplier_key), lines, schedule_date=d(4),
			                                  company=ctx.company)["name"]
			buying.submit_purchase_order(po)
		with clock(d(4), 14):
			gr = buying.receive_against_order(po)["name"]
			buying.submit_goods_receipt(gr)
		with clock(d(5), 10):
			pi = buying.bill_against(purchase_receipt=gr)["name"]
			buying.approve_purchase_invoice(pi)
		bills.append(pi)
	print(f"  bought from {len(by_supplier)} suppliers: {len(bills)} bills")

	# Customer orders on the 3rd.
	sales_orders = []
	with clock(d(2), 12):
		for key, item, qty, _rule in orders:
			so = selling.create_sales_order(_customer(key), [{"item_code": item, "qty": qty,
			                                                  "rate": seed.TRAILERS[item][1]}],
			                                delivery_date=d(24), company=ctx.company)["name"]
			selling.submit_sales_order(so)
			sales_orders.append(so)

	# Build: one work order per order, a stage roughly every other day, QC on the 15th.
	for i, (key, item, qty, _rule) in enumerate(orders):
		set_clock(d(6 + i % 2), 8)
		wo = manufacturing.create_work_order(
			item=item, qty=qty, source_warehouse=ctx.stores, fg_warehouse=ctx.fg,
			planned_start_date=d(6 + i % 2), expected_delivery_date=d(16),
			description=f"For {seed.CUSTOMERS[key][0]}: standard specification, company livery.",
		)["name"]
		for nxt in seed.STAGES[1:5]:
			tick(46)
			manufacturing.advance_stage(wo, notes=seed.STAGE_NOTES[nxt])
			if nxt == "Paint":
				serial_register.mark_embossed(work_order=wo)
		tick(6)
		failed = FAILED_FIRST.get((month_index, i))
		if failed:
			bad, why = failed
			seed_quality._inspect(rng, "Work Order", wo, seed_quality.TRAILER_CHECKLIST, bad=bad, remarks=why)
			tick(22)
		seed_quality._inspect(rng, "Work Order", wo, seed_quality.TRAILER_CHECKLIST,
		                      remarks="All checks within limits. Released to stock.")
		tick(2)
		result = manufacturing.advance_stage(wo, notes=seed.STAGE_NOTES["Delivery"])
		if result.get("stock_warning"):
			frappe.throw(f"{wo}: stock entry did not post: {result['stock_warning']}")
		frappe.db.commit()
		reset_clock()
	print(f"  built {sum(o[2] for o in orders)} trailers on {len(orders)} work orders")

	# Deliver and invoice from the 18th, one trailer per delivery note.
	invoices = []
	for i, ((key, item, qty, rule), so) in enumerate(zip(orders, sales_orders)):
		with clock(d(17 + i), 10):
			free = frappe.get_all("Trailer Serial", filters={"trailer_type": item, "status": "In Stock"},
			                      pluck="name", order_by="creation asc", limit=qty)
			for s in free:
				selling.reserve_serial(so, s)
			for s in free:
				dn = _deliver(so, s)
				si = selling.invoice_delivery(dn)["name"]
				selling.submit_sales_invoice(si)
				invoices.append((si, rule))
				frappe.db.set_value("Trailer Serial", s, {
					"warranty_start_date": d(17 + i), "warranty_expiry_date": add_days(d(17 + i), 730),
				}, update_modified=False)
			frappe.db.commit()
	print(f"  delivered and invoiced {len(invoices)} trailers")

	# Utilities mid-month, suppliers paid on the 21st, salaries and collections at month end.
	with clock(d(14), 10):
		_journal(ctx, [("Water & Electricity", 4100 + 150 * month_index, 0), (None, 0, 4100 + 150 * month_index)],
		         f"Electricity and water, {_month(start)}")
	with clock(d(20), 11):
		for pi in bills:
			seed._pay(pi)
	with clock(d(26), 15):
		_journal(ctx, [("Basic Salary", 86000, 0), (BANK, 0, 86000)],
		         f"Salaries, production and office staff, {_month(start)}")
	with clock(d(27), 11):
		owing = 0
		for si, rule in invoices:
			if rule == "full":
				_receive(si)
			elif rule == "half":
				_receive(si, round(flt(frappe.db.get_value("Sales Invoice", si, "outstanding_amount")) / 2, -2))
			if rule != "full":
				owing += flt(frappe.db.get_value("Sales Invoice", si, "outstanding_amount"))
	print(f"  suppliers paid; customers paid{f', {owing:,.0f} AED still owed' if owing else ' in full'}")


def _capital(ctx):
	"""The owners' capital, before the first month's trading: most to the bank, the rest to the safe."""
	if frappe.db.exists("Journal Entry", {"user_remark": seed.CAPITAL_REMARK, "docstatus": 1}):
		return
	je = _journal(ctx, [(BANK, 2000000, 0), (None, 500000, 0),
	                    (("Capital Stock", "Share Capital"), 0, 2500000)], seed.CAPITAL_REMARK)
	print(f"  {je}  capital 2,500,000 AED")


def _deliver(sales_order, serial):
	dn = selling.dispatch_serial(sales_order, serials=[serial])["name"]
	# Dispatch drafts the order's whole remaining quantity; set it to the one trailer leaving.
	doc = frappe.get_doc("Delivery Note", dn)
	if doc.items[0].qty != 1:
		doc.items[0].qty = 1
		doc.save(ignore_permissions=True)
	selling.submit_delivery_note(dn)
	return dn


def _receive(sales_invoice, amount=None):
	pe = selling.receive_against_invoice(sales_invoice, amount=amount)["name"]
	frappe.get_doc("Payment Entry", pe).submit()
	frappe.db.commit()
	return pe


def _journal(ctx, lines, remark, post=True):
	"""lines: (account name, None for the cash account or BANK for the bank account, debit, credit)."""
	cash, bank = frappe.db.get_value("Company", ctx.company, ["default_cash_account", "default_bank_account"])

	def resolve(name):
		if name is None:
			return cash
		if name is BANK:
			return bank or seed.account(ctx, "Banks Current Accounts")
		return seed.account(ctx, *name) if isinstance(name, tuple) else seed.account(ctx, name)

	rows = [{"account": resolve(name), "debit": dr, "credit": cr,
	         "cost_center": ctx.cost_center} for name, dr, cr in lines]
	je = selling.create_journal_entry(rows, user_remark=remark, company=ctx.company)["name"]
	if post:
		selling.post_journal_entry(je)
	return je


def _month(day):
	return getdate(day).strftime("%B")


# ---------------------------------------------------------------------------
# 6. October: the open picture
# ---------------------------------------------------------------------------

def _october(ctx):
	with clock(OCTOBER, 9):
		seed._journals(ctx)  # its capital journal is skipped: _capital posted it in June
	with clock(add_days(OCTOBER, 1), 10):
		seed._buying(ctx)
	with clock(add_days(OCTOBER, 2), 11):
		orders = seed._selling_orders(ctx)

	# Each work order starts on 4 October and moves a stage every ten hours.
	real_create, real_bto, real_advance = (manufacturing.create_work_order, selling.build_for_order,
	                                       manufacturing.advance_stage)

	def create(*args, **kwargs):
		set_clock(add_days(OCTOBER, 3), 8)
		return real_create(*args, **kwargs)

	def bto(*args, **kwargs):
		set_clock(add_days(OCTOBER, 3), 8)
		return real_bto(*args, **kwargs)

	def advance(*args, **kwargs):
		tick(10)
		return real_advance(*args, **kwargs)

	manufacturing.create_work_order, selling.build_for_order, manufacturing.advance_stage = create, bto, advance
	try:
		seed._production(ctx, orders)
	finally:
		manufacturing.create_work_order, selling.build_for_order, manufacturing.advance_stage = (
			real_create, real_bto, real_advance)
		reset_clock()

	with clock(add_days(OCTOBER, 6), 10):
		seed._selling(ctx, orders)


# ---------------------------------------------------------------------------
# 7. Invoice deletion requests
# ---------------------------------------------------------------------------

def _deletion_requests(ctx):
	from frappe.model.workflow import apply_workflow

	from a3_trading_management.api import invoice_delete

	operator, finance, owner = (f"{k}@{MAIL}" for k in ("operator", "finance", "owner"))

	def as_user(user, fn, *args):
		frappe.set_user(user)
		try:
			return fn(*args)
		finally:
			frappe.set_user("Administrator")

	def decide(request, user, action):
		doc = frappe.get_doc("Invoice Deletion Request", request)
		return as_user(user, apply_workflow, doc, action).workflow_state

	# A bill keyed in twice: raised, approved by Finance and by the Owner -> deleted.
	duplicate = _duplicate_bill(ctx)
	req = as_user(operator, invoice_delete.request_deletion, "Purchase Invoice", duplicate,
	              "Entered twice: the same Voltline delivery is already billed.")["name"]
	decide(req, finance, "Approve")
	state = decide(req, owner, "Approve")
	print(f"  {req}  {duplicate} (duplicate bill) -> {state}")

	octo = frappe.get_all("Sales Invoice", filters={"docstatus": 1, "posting_date": [">=", OCTOBER]},
	                      fields=["name", "customer_name", "outstanding_amount"], order_by="name asc")
	unpaid = [r for r in octo if flt(r.outstanding_amount) > 0]
	if len(unpaid) >= 2:
		req = as_user(operator, invoice_delete.request_deletion, "Sales Invoice", unpaid[0].name,
		              f"Wrong trailer type on the invoice to {unpaid[0].customer_name}; to be re-issued.")["name"]
		print(f"  {req}  {unpaid[0].name} -> Pending Finance")
		req = as_user(operator, invoice_delete.request_deletion, "Sales Invoice", unpaid[1].name,
		              "Customer asked for the invoice in their parent company's name.")["name"]
		state = decide(req, finance, "Approve")
		print(f"  {req}  {unpaid[1].name} -> {state}")

	paid = frappe.get_all("Purchase Invoice", filters={"docstatus": 1, "outstanding_amount": 0,
	                                                    "posting_date": ["<", OCTOBER]}, pluck="name", limit=1)
	if paid:
		req = as_user(operator, invoice_delete.request_deletion, "Purchase Invoice", paid[0],
		              "Rate looked wrong.")["name"]
		state = decide(req, finance, "Reject")
		as_user(finance, frappe.get_doc("Invoice Deletion Request", req).add_comment, "Comment",
		        "Rate matches the supplier's quotation; correct it with a debit note if needed.")
		print(f"  {req}  {paid[0]} -> {state}")
	frappe.db.commit()


def _duplicate_bill(ctx):
	"""A bill for welding wire, keyed in a second time by mistake."""
	pi = frappe.get_doc({
		"doctype": "Purchase Invoice", "company": ctx.company, "supplier": _supplier("electrical"),
		"bill_no": "VL-INV-4471", "bill_date": add_days(OCTOBER, 3),
		"items": [{"item_code": "RM-HARNESS", "qty": 4, "rate": 420, "warehouse": ctx.stores}],
	})
	pi.set_missing_values()
	pi.insert(ignore_permissions=True)
	pi.submit()
	return pi.name


def _finish():
	frappe.db.set_single_value("Website Settings", "home_page", "a3-workshop")
	frappe.clear_cache()
	frappe.db.commit()
