# Copyright (c) 2026, Quantbit Technologies Pvt. Ltd.

import frappe
from frappe.model.document import Document
from frappe import _
from frappe.utils import today, getdate, date_diff


class RentalContract(Document):

    def validate(self):
        self.set_customer_kyc()  # fetch kyc
        self.set_vehicle_status_snapshot()  # snapshot vehicle status

        self.validate_customer_kyc()  # kyc check
        self.validate_licence()  # licence check
        self.validate_vehicle()  # vehicle check
        self.validate_dates()  # date check
        self.validate_km()  # km check
        self.validate_rate_card()  # rate check

        self.set_total_days()  # auto days
        self.set_km_used()  # auto km used
        self.set_rate_type()  # auto rate type
        self.set_flags()  # active/closed flags
        self.update_contract_status()  # update status

    # fetch kyc
    def set_customer_kyc(self):
        if self.customer and not self.customer_kyc:
            kyc = frappe.db.get_value("Customer KYC", {"customer": self.customer}, "name")
            if kyc:
                self.customer_kyc = kyc

    # vehicle status snapshot
    def set_vehicle_status_snapshot(self):
        if self.vehicle:
            status = frappe.db.get_value("Vehicle Master", self.vehicle, "vehicle_status")
            if status:
                self.vehicle_status_at_contract = status

    # kyc validation
    def validate_customer_kyc(self):
        if not self.customer_kyc:
            frappe.throw(_("Customer KYC is required"))
        kyc = frappe.get_doc("Customer KYC", self.customer_kyc)
        if kyc.kyc_status != "Active":
            frappe.throw(_("Customer KYC must be Active"))

    # licence validation
    def validate_licence(self):
        if self.licence_expiry and getdate(self.licence_expiry) < getdate(today()):
            frappe.throw(_("Driving licence is expired"))

    # vehicle validation
    def validate_vehicle(self):
        if not self.vehicle:
            frappe.throw(_("Vehicle is required"))
        status = frappe.db.get_value("Vehicle Master", self.vehicle, "vehicle_status")
        if status in ["On Rent", "Reserved", "Blocked"]:
            frappe.throw(_("Vehicle is not available"))

    # date validation
    def validate_dates(self):
        if self.date_out and self.date_return:
            if getdate(self.date_return) <= getdate(self.date_out):
                frappe.throw(_("Return date must be after start date"))

    # km validation
    def validate_km(self):
        if self.km_out is not None and self.km_out < 0:
            frappe.throw(_("KM cannot be negative"))

    # rate card validation
    def validate_rate_card(self):
        if self.rate_card:
            category = frappe.db.get_value("Vehicle Master", self.vehicle, "vehicle_category")
            rate_category = frappe.db.get_value("Rate Card", self.rate_card, "vehicle_category")
            if rate_category != category and rate_category != "Corporate (any)":
                frappe.throw(_("Rate card does not match vehicle category"))

    # total days
    def set_total_days(self):
        if self.date_out:
            end_date = self.actual_return_date or self.date_return
            if end_date:
                self.total_days = date_diff(end_date, self.date_out)

    # km used
    def set_km_used(self):
        if self.km_out is not None and self.km_return is not None:
            self.km_used = self.km_return - self.km_out

    # rate type
    def set_rate_type(self):
        if self.contract_type in ["Daily", "Weekly", "Monthly"]:
            self.rate_type_used = self.contract_type

    # flags
    def set_flags(self):
        self.is_active = 1
        self.is_closed = 1 if self.contract_status == "Closed" else 0

    # status logic
    def update_contract_status(self):
        if self.docstatus == 1 and not self.actual_return_date:
            self.contract_status = "Active"
        if self.actual_return_date and self.km_return:
            self.contract_status = "Pending Return"
        if self.contract_status == "Closed":
            self.is_closed = 1

    # on submit
    def on_submit(self):
        frappe.db.set_value("Vehicle Master", self.vehicle, {
            "vehicle_status": "On Rent",
            "current_contract": self.name
        })
        self.contract_status = "Active"
        self.is_active = 1

    # on cancel
    def on_cancel(self):
        frappe.db.set_value("Vehicle Master", self.vehicle, {
            "vehicle_status": "Available",
            "current_contract": None
        })
        self.contract_status = "Cancelled"
        self.is_active = 0