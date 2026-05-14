# Copyright (c) 2026, Quantbit Technologies Pvt. Ltd.

import frappe
from frappe.model.document import Document
from frappe.utils import today, getdate, flt


class PDCRegister(Document):

    def before_save(self):
        # Capture prev_status before ERPNext writes new value — works for draft saves
        self._prev_status = frappe.db.get_value("PDC Register", self.name, "pdc_status") if not self.is_new() else None

    def before_submit(self):
        # Capture prev_status for submit flow (before_save doesn't fire on submit)
        self._prev_status = frappe.db.get_value("PDC Register", self.name, "pdc_status") if not self.is_new() else None
        if self.pdc_status not in ("Collected - Held", "Submitted to Bank"):
            frappe.throw("PDC can only be submitted when status is 'Collected - Held' or 'Submitted to Bank'.")

    def before_update_after_submit(self):
        # Capture prev_status before ERPNext writes updated submitted doc
        self._prev_status = frappe.db.get_value("PDC Register", self.name, "pdc_status")

    def validate(self):
        self._validate_cheque_date()
        self._validate_duplicate_cheque()
        self._validate_amount()
        # Use getattr with None default so it never raises AttributeError
        prev = getattr(self, "_prev_status", None)
        if prev and prev != self.pdc_status:
            self._validate_status_transitions(prev)

    def on_submit(self):
        if not self.date_collected:
            self.db_set("date_collected", today())

    def on_cancel(self):
        self.db_set("pdc_status", "Cancelled")

    def on_update_after_submit(self):
        prev = getattr(self, "_prev_status", None)
        if not prev:
            return

        # Auto-increment representation_count when bounced cheque is re-presented
        if prev == "Bounced" and self.pdc_status == "Submitted to Bank":
            new_count = int(frappe.db.get_value("PDC Register", self.name, "representation_count") or 0) + 1
            self.db_set("representation_count", new_count)
            frappe.msgprint(f"✅ Cheque re-presented. Representation count: {new_count}", alert=True)

        # Auto-create clearance JV when marked Cleared
        if self.pdc_status == "Cleared" and not self.erpnext_journal_entry:
            if not self.realisation_date:
                self.db_set("realisation_date", today())
                self.realisation_date = today()
            self._create_clearance_jv()

    # ── Validators ──────────────────────────────────────────────────────────

    def _validate_cheque_date(self):
        if self.cheque_post_date and getdate(self.cheque_post_date) < getdate(today()):
            frappe.msgprint(
                f"⚠️ Cheque date {self.cheque_post_date} is in the past — confirm this is intentional.",
                alert=True,
            )

    def _validate_duplicate_cheque(self):
        existing = frappe.db.exists(
            "PDC Register",
            {
                "cheque_number": self.cheque_number,
                "issuing_bank":  self.issuing_bank,
                "customer":      self.customer,
                "name":          ["!=", self.name],
                "pdc_status":    ["not in", ("Cancelled", "Returned to Customer")],
            },
        )
        if existing:
            frappe.throw(
                f"Cheque #{self.cheque_number} from {self.issuing_bank} already exists for this customer: {existing}"
            )

    def _validate_amount(self):
        if flt(self.cheque_amount) <= 0:
            frappe.throw("Cheque Amount must be greater than zero.")

    def _validate_status_transitions(self, prev_status):
        allowed = {
            "Collected - Held":     {"Submitted to Bank", "Returned to Customer", "Cancelled"},
            "Submitted to Bank":    {"Cleared", "Bounced", "Cancelled"},
            "Cleared":              set(),
            "Bounced":              {"Submitted to Bank", "Returned to Customer", "Cancelled"},
            "Returned to Customer": set(),
            "Cancelled":            set(),
        }
        next_allowed = allowed.get(prev_status, set())
        if self.pdc_status not in next_allowed:
            frappe.throw(
                f"Invalid status transition: '{prev_status}' → '{self.pdc_status}'. "
                f"Allowed: {', '.join(next_allowed) or 'none — terminal state'}."
            )

    # ── Clearance JV: Dr Bank / Cr Accounts Receivable ──────────────────────

    def _create_clearance_jv(self):
        company      = frappe.defaults.get_global_default("company")
        bank_account = frappe.db.get_value("Company", company, "default_bank_account")
        receivable   = frappe.db.get_value("Company", company, "default_receivable_account")

        if not bank_account or not receivable:
            frappe.log_error(
                f"PDC {self.name}: clearance JV skipped — default bank or receivable account not set on Company.",
                "PDC Register"
            )
            frappe.msgprint(
                "⚠️ Clearance JV could not be created — default Bank Account or Receivable Account "
                "is not configured on the Company record. Please create the JV manually.",
                title="JV Skipped", indicator="orange"
            )
            return

        jv = frappe.new_doc("Journal Entry")
        jv.voucher_type = "Bank Entry"
        jv.posting_date = self.realisation_date or today()
        jv.cheque_no    = self.cheque_number
        jv.cheque_date  = self.cheque_post_date
        jv.user_remark  = f"PDC Clearance: {self.name} | Customer: {self.customer}"

        jv.append("accounts", {
            "account":                   bank_account,
            "debit_in_account_currency": flt(self.cheque_amount)
            # "party_type":                "Customer",
            # "party":                     self.customer,
        })
        jv.append("accounts", {
            "account":                    receivable,
            "credit_in_account_currency": flt(self.cheque_amount),
            "party_type":                 "Customer",
            "party":                      self.customer,
        })

        try:
            jv.insert(ignore_permissions=True)
            jv.submit()
            self.db_set("erpnext_journal_entry", jv.name)
            frappe.msgprint(
                f"✅ Clearance Journal Entry <b>{jv.name}</b> created and submitted.<br>"
                f"Dr {bank_account} / Cr {receivable} — OMR {flt(self.cheque_amount):,.3f}",
                title="JV Created", indicator="green"
            )
        except Exception:
            frappe.log_error(frappe.get_traceback(), f"PDC {self.name}: JV creation failed")
            frappe.throw(
                "Clearance JV creation failed — check the Error Log for details.",
                title="JV Error"
            )


# ── Whitelisted APIs ─────────────────────────────────────────────────────────

@frappe.whitelist()
def get_pdcs_for_contract(rental_contract: str):
    return frappe.db.get_all(
        "PDC Register",
        filters={"rental_contract": rental_contract},
        fields=[
            "name", "cheque_number", "issuing_bank", "cheque_amount",
            "cheque_post_date", "pdc_status", "realisation_date",
            "bounce_reason", "representation_count", "erpnext_journal_entry",
        ],
        order_by="cheque_post_date asc",
    )


@frappe.whitelist()
def get_pdcs_for_customer(customer: str):
    return frappe.db.get_all(
        "PDC Register",
        filters={
            "customer":   customer,
            "pdc_status": ["not in", ("Cancelled", "Returned to Customer")],
        },
        fields=[
            "name", "cheque_number", "issuing_bank", "cheque_amount",
            "cheque_post_date", "pdc_status", "rental_contract",
        ],
        order_by="cheque_post_date asc",
    )


@frappe.whitelist()
def submit_pdc_to_bank(pdc_name: str, bank_reference: str, submitted_by: str):
    doc = frappe.get_doc("PDC Register", pdc_name)
    if doc.pdc_status != "Collected - Held":
        frappe.throw("Only 'Collected - Held' PDCs can be submitted to bank.")
    doc.pdc_status             = "Submitted to Bank"
    doc.bank_reference_number  = bank_reference
    doc.submitted_by           = submitted_by
    doc.date_submitted_to_bank = today()
    doc.save(ignore_permissions=True)
    return doc.name


@frappe.whitelist()
def get_rental_contracts_for_customer(doctype, txt, searchfield, start, page_len, filters):
    # Called from rental_contract field query — filters contracts by selected customer
    if isinstance(filters, str):
        import json
        filters = json.loads(filters)
    customer = filters.get("customer") if filters else None
    if not customer:
        return []
    return frappe.db.sql("""
        SELECT name, vehicle, date_out, date_return, contract_status
        FROM `tabRental Contract`
        WHERE customer = %(customer)s
          AND (name LIKE %(txt)s OR vehicle LIKE %(txt)s)
          AND docstatus = 1
        ORDER BY date_out DESC
        LIMIT %(start)s, %(page_len)s
    """, {
        "customer":  customer,
        "txt":       f"%{txt}%",
        "start":     start,
        "page_len":  page_len,
    })