# Copyright (c) 2026, Quantbit Technologies Pvt. Ltd.

import frappe
from frappe.model.document import Document
from frappe import _
from frappe.utils import today, getdate, add_days


class CustomerKYC(Document):

    def validate(self):
        self.set_full_name()  # auto fill name
        self.validate_mobile()
        self.validate_customer_type_fields()
        self.validate_unique_individual()
        self.validate_blacklist()
        self.validate_duplicate_mobile()
        self.validate_numbers()
        self.validate_pdc()
        self.validate_dates()

    # auto set full name
    def set_full_name(self):
        if self.customer and not self.full_name:
            name = frappe.db.get_value("Customer", self.customer, "customer_name")
            if name:
                self.full_name = name

    # mobile validation
    def validate_mobile(self):
        if not self.mobile_number:
            frappe.throw(_("Mobile Number is required"))

        if self.mobile_number and len(self.mobile_number) < 8:
            frappe.throw(_("Enter valid Mobile Number"))

    # customer type validation
    def validate_customer_type_fields(self):

        if self.customer_type == "Individual":
            if not self.full_name:
                frappe.throw(_("Full Name is required"))

            if not self.id_number:
                frappe.throw(_("ID Number is required"))

            if not self.licence_number:
                frappe.throw(_("Licence Number is required"))

        elif self.customer_type == "Corporate":
            if not self.company_name:
                frappe.throw(_("Company Name is required"))

        elif self.customer_type == "Broker / Travel Agency":
            if not self.company_name:
                frappe.throw(_("Company Name is required"))

            if not self.commission_type:
                frappe.throw(_("Commission Type is required"))

            if self.commission_type == "Percentage of Revenue" and not self.commission_rate:
                frappe.throw(_("Commission Rate is required"))

    # blacklist validation
    def validate_blacklist(self):
        if self.kyc_status == "Blacklisted" and not self.blacklist_reason:
            frappe.throw(_("Blacklist Reason is required"))

    # duplicate mobile for individual
    def validate_duplicate_mobile(self):
        if self.customer_type == "Individual" and self.mobile_number:

            existing = frappe.db.exists(
                "Customer KYC",
                {
                    "mobile_number": self.mobile_number,
                    "customer_type": "Individual",
                    "name": ["!=", self.name],
                },
            )

            if existing:
                frappe.throw(_("Mobile already used for another Individual"))

    # unique individual KYC
    def validate_unique_individual(self):
        if self.customer_type == "Individual" and self.id_number:

            existing = frappe.db.exists(
                "Customer KYC",
                {
                    "id_number": self.id_number,
                    "customer_type": "Individual",
                    "name": ["!=", self.name],
                },
            )

            if existing:
                frappe.throw(_("Individual already has KYC record"))

    # number validation
    def validate_numbers(self):
        if self.credit_limit is not None and self.credit_limit < 0:
            frappe.throw(_("Credit limit cannot be negative"))

        if self.credit_period_days and self.credit_period_days < 0:
            frappe.throw(_("Credit period cannot be negative"))

    # pdc validation
    def validate_pdc(self):
        if self.pdc_required and (not self.pdc_advance_months or self.pdc_advance_months <= 0):
            frappe.throw(_("Enter valid PDC advance months"))

    # date validation (FIXED)
    def validate_dates(self):

        if self.licence_expiry_date:
            if getdate(self.licence_expiry_date) < getdate(today()):
                frappe.throw(_("Licence is expired"))

        if self.id_expiry_date:
            if getdate(self.id_expiry_date) < getdate(today()):
                frappe.msgprint(_("ID already expired"))

            elif getdate(self.id_expiry_date) <= add_days(getdate(today()), 14):
                frappe.msgprint(_("ID will expire within 14 days"))