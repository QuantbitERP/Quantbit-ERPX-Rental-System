# Copyright (c) 2026, Quantbit Technologies Pvt. Ltd.

import frappe
from frappe.model.document import Document
from frappe import _


class SecurityDepositEntry(Document):

    def validate(self):
        self.validate_amount()  # check amount
        self.validate_accounts()  # check bank account

    # validate deposit amount
    def validate_amount(self):
        if not self.deposit_amount or self.deposit_amount <= 0:
            frappe.throw(_("Deposit amount must be greater than 0"))

    # validate bank account
    def validate_accounts(self):
        if not self.bank_account:
            frappe.throw(_("Please select Bank/Cash Account"))

    # on submit create journal entry
    def on_submit(self):
        je = self.create_journal_entry()
        self.gl_journal_entry = je.name  # store JE

        # link to rental contract
        if self.rental_contract:
            frappe.db.set_value(
                "Rental Contract",
                self.rental_contract,
                "deposit_journal_entry",
                je.name
            )

    # create journal entry
    def create_journal_entry(self):

        company = frappe.defaults.get_user_default("Company")

        debit_account = self.bank_account  # selected bank/cash
        credit_account = frappe.db.get_value(
    "Account",
    {
        "account_name": "Customer Deposit Payable",
        "company": frappe.defaults.get_user_default("Company")
    },
    "name"
)  # liability account (must exist)

        je = frappe.new_doc("Journal Entry")
        je.voucher_type = "Journal Entry"
        je.company = company
        je.posting_date = self.collection_date  # ✅ FIX

        # debit entry
        je.append("accounts", {
            "account": debit_account,
            "debit_in_account_currency": self.deposit_amount
        })

        # credit entry
        je.append("accounts", {
    "account": credit_account,
    "credit_in_account_currency": self.deposit_amount
})

        je.insert()
        je.submit()

        return je