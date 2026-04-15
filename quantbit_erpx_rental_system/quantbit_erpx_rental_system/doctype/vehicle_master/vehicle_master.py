# Copyright (c) 2026, Quantbit Technologies Pvt. Ltd. and contributors
# For license information, please see license.txt

import frappe
from frappe.model.document import Document
from frappe import _
from frappe.utils import today


class VehicleMaster(Document):

    def validate(self):
        self.validate_plate()  # plate check
        self.validate_year()  # year check
        self.validate_odometer()  # km check
        self.validate_dates()  # expiry check
        self.validate_finance()  # loan check

    # plate validation
    def validate_plate(self):
        if not self.plate_number:
            frappe.throw(_("Plate Number is required"))

    # year validation
    def validate_year(self):
        if self.year_of_manufacture and self.year_of_manufacture > int(today()[:4]):
            frappe.throw(_("Invalid manufacturing year"))

    # odometer validation
    def validate_odometer(self):
        if self.current_odometer_km is not None and self.current_odometer_km < 0:
            frappe.throw(_("Odometer cannot be negative"))

    # expiry validation
    def validate_dates(self):
        if self.mulkiya_expiry_date and self.mulkiya_expiry_date < today():
            frappe.msgprint(_("Mulkiya expired"))

        if self.insurance_expiry_date and self.insurance_expiry_date < today():
            frappe.msgprint(_("Insurance expired"))

    # finance validation
    def validate_finance(self):
        if self.loan_start_date and self.loan_end_date:
            if self.loan_end_date <= self.loan_start_date:
                frappe.throw(_("Loan end date must be after start date"))