# Copyright (c) 2026, Quantbit Technologies Pvt. Ltd. and contributors
# For license information, please see license.txt

import frappe
from frappe.model.document import Document
from frappe import _


class RateCard(Document):

    def validate(self):
        self.validate_rates()  # rate check
        self.validate_km()  # km check
        self.validate_charges()  # charges check

    # rate validation
    def validate_rates(self):
        if not (self.daily_rate or self.weekly_rate or self.monthly_rate):
            frappe.throw(_("At least one rate must be set"))

    # km validation
    def validate_km(self):
        if self.free_km_per_day and self.free_km_per_day < 0:
            frappe.throw(_("Invalid KM per day"))

        if self.free_km_per_week and self.free_km_per_week < 0:
            frappe.throw(_("Invalid KM per week"))

        if self.free_km_per_month and self.free_km_per_month < 0:
            frappe.throw(_("Invalid KM per month"))

    # charge validation
    def validate_charges(self):
        if self.excess_km_charge_daily and self.excess_km_charge_daily < 0:
            frappe.throw(_("Invalid daily excess charge"))

        if self.excess_km_charge_weekly and self.excess_km_charge_weekly < 0:
            frappe.throw(_("Invalid weekly excess charge"))

        if self.excess_km_charge_monthly and self.excess_km_charge_monthly < 0:
            frappe.throw(_("Invalid monthly excess charge"))