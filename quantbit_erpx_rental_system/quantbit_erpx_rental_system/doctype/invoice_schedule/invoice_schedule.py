# Copyright (c) 2026, Quantbit Technologies Pvt. Ltd. and contributors

import frappe
from frappe.model.document import Document
from frappe import _
from frappe.utils import add_days


class InvoiceSchedule(Document):

    def validate(self):
        self.calculate_totals()  # calculate total

    # calculate total (VAT optional)
    def calculate_totals(self):
        if self.billing_amount:
            if self.vat_rate:
                rate = float(self.vat_rate.split("%")[0])  # extract VAT %
                self.vat_amount = (self.billing_amount * rate) / 100  # calc VAT
                self.total_billing_amount = self.billing_amount + self.vat_amount  # total with VAT
            else:
                self.total_billing_amount = self.billing_amount  # total without VAT

    # create invoice
    def create_invoice(self):

        company = frappe.defaults.get_user_default("Company")  # get company

        # get rental contract
        rc = frappe.get_doc("Rental Contract", self.rental_contract)

        # create sales invoice
        si = frappe.new_doc("Sales Invoice")
        si.customer = self.customer
        si.posting_date = self.next_billing_date

        # add item row
        si.append("items", {
            "item_name": "Vehicle Rental Charges",
            "qty": 1,
            "rate": self.billing_amount
        })

        # optional VAT config apply
        vat = frappe.get_all(
            "VAT Configuration",
            filters={"company": company, "is_active": 1},
            fields=["sales_tax_template"],
            limit=1
        )

        if vat and vat[0].sales_tax_template:
            si.taxes_and_charges = vat[0].sales_tax_template  # apply tax template

        si.insert()
        si.submit()

        # update schedule fields
        self.last_invoice = si.name
        self.last_invoice_date = self.next_billing_date
        self.total_invoices_generated = (self.total_invoices_generated or 0) + 1  # increment count

        # update next billing date
        if self.billing_frequency == "Monthly":
            self.next_billing_date = add_days(self.next_billing_date, 30)
        elif self.billing_frequency == "Weekly":
            self.next_billing_date = add_days(self.next_billing_date, 7)
        elif self.billing_frequency == "Fortnightly":
            self.next_billing_date = add_days(self.next_billing_date, 14)

        self.save()

        return si.name