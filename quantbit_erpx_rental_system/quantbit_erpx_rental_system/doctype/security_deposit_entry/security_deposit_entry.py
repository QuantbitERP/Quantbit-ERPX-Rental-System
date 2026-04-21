# Copyright (c) 2026, Quantbit Technologies Pvt. Ltd.

import frappe
from frappe.model.document import Document


class SecurityDepositEntry(Document):

    def validate(self):
        self.validate_amount()  # ensure amount > 0
        self.validate_customer()  # ensure customer present
        self.validate_contract()  # ensure contract linked

    # ---------------- VALIDATIONS ---------------- #

    def validate_amount(self):
        if not self.deposit_amount or self.deposit_amount <= 0:
            frappe.throw("Deposit amount must be greater than zero")

    def validate_customer(self):
        if not self.customer:
            frappe.throw("Customer is required")

    def validate_contract(self):
        if not self.rental_contract:
            frappe.throw("Rental Contract is required")

    # ---------------- MAIN LOGIC ---------------- #

    def on_submit(self):

        # fetch company from Rental Contract
        company = frappe.db.get_value(
            "Rental Contract",
            self.rental_contract,
            "company"
        ) or frappe.defaults.get_user_default("Company")

        if not self.bank_account:
            frappe.throw("Bank / Cash Account is required")

        # 🔹 Fetch ONLY correct ledger under Current Liabilities
        deposit_accounts = frappe.get_all(
            "Account",
            filters={
                "company": company,
                "is_group": 0,
                "disabled": 0,
                "parent_account": ["like", "%Current Liabilities%"]
            },
            fields=["name", "account_name"]
        )

        # 🔹 Pick exact match safely
        deposit_account = next(
            (
                acc["name"]
                for acc in deposit_accounts
                if acc["account_name"].strip().lower() == "customer deposit payable"
            ),
            None
        )

        if not deposit_account:
            frappe.throw("Customer Deposit Payable ledger not found under Current Liabilities")

        # 🔹 Create Journal Entry
        je = frappe.new_doc("Journal Entry")
        je.voucher_type = "Journal Entry"
        je.company = company
        je.posting_date = self.collection_date
        je.remark = f"Security deposit received for Rental Contract {self.rental_contract}"

        # 🔹 Debit Bank / Cash
        je.append("accounts", {
            "account": self.bank_account,
            "debit_in_account_currency": self.deposit_amount,
            "credit_in_account_currency": 0
        })

        # 🔹 Credit Deposit Liability
        je.append("accounts", {
            "account": deposit_account,
            "party_type": "Customer",
            "party": self.customer,
            "credit_in_account_currency": self.deposit_amount,
            "debit_in_account_currency": 0
        })

        # 🔹 Save & Submit JE
        je.insert(ignore_permissions=True)
        je.submit()

        frappe.db.commit()  # ensure DB save

        # 🔹 Link JE back
        self.gl_journal_entry = je.name

        # 🔹 Set status
        self.deposit_status = "Held"

        # ================= NEW LOGIC START ================= #

        # 🔹 update Rental Contract (deposit tracking)
        contract = frappe.get_doc("Rental Contract", self.rental_contract)

        contract.deposit_applied = (contract.deposit_applied or 0) + self.deposit_amount
        contract.deposit_journal_entry = je.name

        contract.save(ignore_permissions=True)

        # ================= NEW LOGIC END ================= #

        frappe.msgprint(f"✅ Security Deposit Recorded: {je.name}")