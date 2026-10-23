"""Employee Overtime — hours an employee worked beyond their shift, entered by the
Supervisor (item 4 of the Supervisor role) and submitted to make it final. Only HR
can cancel one. Payroll does not read it yet; HR totals it with the report view.

The Supervisor may only pick an Employee in the link (Employee holds salary and
personal details), so the name, department and company are filled in here on the
server rather than fetched in the browser.
"""

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import flt, time_diff_in_hours

MAX_HOURS = 16


class EmployeeOvertime(Document):
	def validate(self):
		employee = frappe.db.get_value("Employee", self.employee,
		                               ["employee_name", "department", "company", "status"], as_dict=True)
		if not employee:
			frappe.throw(_("Employee {0} does not exist.").format(self.employee))
		if employee.status != "Active":
			frappe.throw(_("{0} is not an active employee.").format(employee.employee_name))
		self.employee_name, self.department, self.company = employee.employee_name, employee.department, employee.company
		self._set_hours()
		self._check_duplicate()

	def _set_hours(self):
		# Both times default to the moment the form was opened, a split second apart;
		# times less than a minute apart were not entered, and the hours typed in stand.
		hours = time_diff_in_hours(self.to_time, self.from_time) if self.from_time and self.to_time else 0
		if abs(hours) >= 1 / 60:
			self.hours = flt(hours + 24 if hours < 0 else hours, 2)  # past midnight
		if flt(self.hours) <= 0:
			frappe.throw(_("Enter the overtime hours, or the From and To times."))
		if flt(self.hours) > MAX_HOURS:
			frappe.throw(_("{0} hours of overtime in one day is more than the {1} allowed.").format(
				self.hours, MAX_HOURS))

	def _check_duplicate(self):
		other = frappe.db.get_value("Employee Overtime", {
			"employee": self.employee, "overtime_date": self.overtime_date,
			"docstatus": ["<", 2], "name": ["!=", self.name],
		})
		if other:
			frappe.throw(_("{0} already has overtime entered for {1}: {2}.").format(
				self.employee_name, frappe.format(self.overtime_date, "Date"), other))
