# Stage-transition audit line (child table) on a Work Order. One row is appended
# by a3_trading_management.api.manufacturing every time the custom production stage advances
# (Material -> Fabrication -> Assembly -> Paint -> QC -> Delivery), recording the
# user, timestamp and time spent in the previous stage.

import frappe
from frappe.model.document import Document


class WorkshopWorkOrderStageLog(Document):
	pass
