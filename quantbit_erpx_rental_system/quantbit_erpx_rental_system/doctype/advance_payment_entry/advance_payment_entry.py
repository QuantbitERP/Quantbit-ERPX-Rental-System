# Copyright (c) 2026, Quantbit Technologies Pvt. Ltd.

import frappe
from frappe.model.document import Document
from frappe import _


class AdvancePaymentEntry(Document):

    def validate(self):
        self.validate_amount()  # check amount
        self.validate_account()  # check bank

    # validate amount
    def validate_amount(self):
        if not self.advance_amount or self.advance_amount <= 0:
            frappe.throw(_("Advance amount must be greater than 0"))

    # validate bank account
    def validate_account(self):
        if not self.bank_account:
            frappe.throw(_("Please select Bank/Cash Account"))

    # on submit
    def on_submit(self):
        je = self.create_journal_entry()
        self.gl_journal_entry = je.name  # store JE

        # link with rental contract
        if self.rental_contract:
            frappe.db.set_value(
                "Rental Contract",
                self.rental_contract,
                "advance_journal_entry",
                je.name
            )

    # get advance account safely
    def get_advance_account(self, company):

        account = frappe.db.get_value(
            "Account",
            {
                "name": ["like", "Advance Rent Received%"],
                "company": company,
                "is_group": 0
            },
            "name"
        )

        if not account:
            frappe.throw(
                _("Please create 'Advance Rent Received' account under Current Liabilities")
            )

        return account

    # create journal entry
    def create_journal_entry(self):

        company = frappe.defaults.get_user_default("Company")

        debit_account = self.bank_account  # asset account
        credit_account = self.get_advance_account(company)  # liability account

        je = frappe.new_doc("Journal Entry")
        je.voucher_type = "Journal Entry"
        je.company = company
        je.posting_date = self.payment_date

        # debit (cash/bank)
        je.append("accounts", {
            "account": debit_account,
            "debit_in_account_currency": self.advance_amount
        })

        # credit (advance liability)
        je.append("accounts", {
            "account": credit_account,
            "credit_in_account_currency": self.advance_amount
        })

        je.insert()
        je.submit()

        return je