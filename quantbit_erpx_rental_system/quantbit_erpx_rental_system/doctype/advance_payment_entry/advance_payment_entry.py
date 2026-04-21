# Copyright (c) 2026, Quantbit Technologies Pvt. Ltd.

import frappe
from frappe.model.document import Document


class AdvancePaymentEntry(Document):

    def validate(self):
        self.validate_amount()  # ensure amount > 0
        self.validate_customer()  # ensure customer present
        self.validate_contract()  # ensure contract linked

    # ---------------- VALIDATIONS ---------------- #

    def validate_amount(self):
        if not self.advance_amount or self.advance_amount <= 0:
            frappe.throw("Advance amount must be greater than zero")

    def validate_customer(self):
        if not self.customer:
            frappe.throw("Customer is required")

    def validate_contract(self):
        if not self.rental_contract:
            frappe.throw("Rental Contract is required")

    # ---------------- MAIN LOGIC ---------------- #

    def on_submit(self):

        # 🔹 fetch company from Rental Contract (correct source)
        company = frappe.db.get_value(
            "Rental Contract",
            self.rental_contract,
            "company"
        ) or frappe.defaults.get_user_default("Company")

        if not self.bank_account:
            frappe.throw("Bank / Cash Account is required")

        # 🔹 fetch advance liability account dynamically (SAFE + CORRECT FIX)
        advance_accounts = frappe.get_all(
            "Account",
            filters={
                "company": company,
                "is_group": 0,
                "disabled": 0,
                "root_type": "Liability"
            },
            fields=["name", "account_name"]
        )

        # 🔹 pick exact account safely
        advance_account = next(
            (
                acc["name"]
                for acc in advance_accounts
                if acc["account_name"].strip().lower() == "advance rent received"
            ),
            None
        )

        if not advance_account:
            frappe.throw("Advance Rent Received ledger not found under Liabilities")

        # 🔹 create Journal Entry
        je = frappe.new_doc("Journal Entry")
        je.voucher_type = "Journal Entry"
        je.company = company
        je.posting_date = self.payment_date
        je.remark = f"Advance received for Rental Contract {self.rental_contract}"

        # 🔹 Debit Bank / Cash (money received)
        je.append("accounts", {
            "account": self.bank_account,
            "debit_in_account_currency": self.advance_amount,
            "credit_in_account_currency": 0
        })

        # 🔹 Credit Advance Liability (customer-wise)
        je.append("accounts", {
            "account": advance_account,
            "party_type": "Customer",
            "party": self.customer,
            "is_advance": "Yes",   # ✅ REQUIRED
            "credit_in_account_currency": self.advance_amount,
            "debit_in_account_currency": 0
        })

        # 🔹 save & submit JE
        je.insert(ignore_permissions=True)
        je.submit()

        frappe.db.commit()  # ensure DB save

        # 🔹 link JE back
        self.gl_journal_entry = je.name

        # 🔹 set balance
        self.balance_remaining = self.advance_amount

        # 🔹 set status
        self.advance_status = "Held"

        # ================= NEW LOGIC START ================= #

        # 🔹 update Rental Contract (advance tracking)
        contract = frappe.get_doc("Rental Contract", self.rental_contract)

        contract.advance_applied = (contract.advance_applied or 0) + self.advance_amount
        contract.advance_journal_entry = je.name

        contract.save(ignore_permissions=True)

        # ================= NEW LOGIC END ================= #

        frappe.msgprint(f"✅ Advance Payment Recorded: {je.name}")