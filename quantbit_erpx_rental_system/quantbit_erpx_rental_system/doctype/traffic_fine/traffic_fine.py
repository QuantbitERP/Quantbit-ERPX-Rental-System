# Copyright (c) 2026, Quantbit Technologies Pvt. Ltd.
# traffic_fine.py

import frappe
import uuid
from frappe.model.document import Document
from frappe.utils import today, flt


# ─────────────────────────────────────────────────────────────────────────────
#  ITEM CATALOGUE — auto-created on first use, never causes "Item not found"
# ─────────────────────────────────────────────────────────────────────────────
FINE_ITEMS = {
    "fine_recovery": {
        "item_code" : "Traffic Fine Recovery",
        "item_name" : "Traffic Fine Recovery Charge",
        "description": "Recovery of ROP traffic fine charged to customer",
    }
}


def _ensure_item_exists(item_key: str, income_account: str, cost_center: str):
    meta = FINE_ITEMS[item_key]
    code = meta["item_code"]
    if frappe.db.exists("Item", code):
        return
    frappe.logger().info(f"[TrafficFine] Auto-creating missing Item '{code}'")
    item = frappe.new_doc("Item")
    item.item_code        = code
    item.item_name        = meta["item_name"]
    item.description      = meta["description"]
    item.item_group       = "Services"
    item.stock_uom        = "Nos"
    item.is_stock_item    = 0
    item.is_sales_item    = 1
    item.is_purchase_item = 0
    if income_account:
        item.append("item_defaults", {
            "company"        : frappe.defaults.get_global_default("company"),
            "income_account" : income_account,
            "cost_center"    : cost_center,
        })
    item.insert(ignore_permissions=True)
    frappe.db.commit()


# ─────────────────────────────────────────────────────────────────────────────
#  DOCTYPE CLASS
# ─────────────────────────────────────────────────────────────────────────────
class TrafficFine(Document):

    # ─────────────────────────────────────────
    #  VALIDATE
    # ─────────────────────────────────────────
    def validate(self):
        self.auto_match_contract()
        self.validate_recovery_decision()
        self.set_cost_centre()

    def auto_match_contract(self):
        """
        Find a submitted Rental Contract where:
          • vehicle matches
          • fine_date falls within date_out to actual_return_date (or date_return)
        Skip if staff has already manually assigned a contract.
        """
        if self.match_method == "Manually Assigned":
            # Re-fetch customer from manually set contract
            if self.matched_contract:
                self.customer_at_fine_date = frappe.db.get_value(
                    "Rental Contract", self.matched_contract, "customer"
                )
            return

        if not self.vehicle or not self.fine_date:
            return

        contract = frappe.db.sql("""
            SELECT name, customer
            FROM   `tabRental Contract`
            WHERE  vehicle   = %(vehicle)s
              AND  docstatus = 1
              AND  date_out <= %(fine_date)s
              AND  IFNULL(actual_return_date, date_return) >= %(fine_date)s
            ORDER  BY date_out DESC
            LIMIT  1
        """, {"vehicle": self.vehicle, "fine_date": self.fine_date}, as_dict=True)

        if contract:
            self.matched_contract      = contract[0].name
            self.customer_at_fine_date = contract[0].customer
            self.match_method          = "Auto-Matched"
        else:
            self.matched_contract      = None
            self.customer_at_fine_date = None
            self.match_method          = "No Match - Internal"
            # Default unmatched to internal — staff can override
            if self.recovery_decision in ("Pending Review", None, ""):
                self.recovery_decision = "Absorb Internally"

    def validate_recovery_decision(self):
        if self.recovery_decision == "Charge to Customer":
            if not self.matched_contract:
                frappe.throw(
                    "⛔ Cannot charge to customer — no Rental Contract was matched "
                    "for this vehicle on the fine date.<br><br>"
                    "Options:<br>"
                    "• Assign a contract manually in the <b>Matched Rental Contract</b> "
                    "field and set Match Method to <b>Manually Assigned</b><br>"
                    "• Change decision to <b>Absorb Internally</b>",
                    title="No Contract Match"
                )
            if not self.customer_at_fine_date:
                frappe.throw(
                    "⛔ Customer could not be determined from the matched contract. "
                    "Please verify the contract record.",
                    title="Customer Missing"
                )

    def set_cost_centre(self):
        if not self.cost_centre:
            company = frappe.defaults.get_global_default("company")
            if company:
                self.cost_centre = frappe.get_cached_value(
                    "Company", company, "cost_center"
                )

    # ─────────────────────────────────────────
    #  BEFORE SUBMIT
    # ─────────────────────────────────────────
    def before_submit(self):
        if self.recovery_decision == "Pending Review":
            frappe.throw(
                "⛔ Please set a <b>Recovery Decision</b> before submitting.<br>"
                "Options: <b>Charge to Customer / Absorb Internally / Under Dispute</b>",
                title="Decision Required"
            )

    # ─────────────────────────────────────────
    #  ON SUBMIT
    # ─────────────────────────────────────────
    def on_submit(self):
        if self.recovery_decision == "Charge to Customer":
            self._create_recovery_invoice()
            self._update_contract_fine_total()

        elif self.recovery_decision == "Absorb Internally":
            self._post_internal_gl()
            self.db_set("recovery_status", "Written Off")

        elif self.recovery_decision == "Under Dispute":
            self._create_dispute_doc()

    # ─────────────────────────────────────────
    #  ON CANCEL
    # ─────────────────────────────────────────
    def on_cancel(self):
        # Reverse contract fine total
        if self.matched_contract and self.recovery_decision == "Charge to Customer":
            self._update_contract_fine_total()   # fresh SUM will drop this fine (docstatus=2)

        # Cancel recovery invoice if exists
        if self.recovery_invoice:
            try:
                inv = frappe.get_doc("Sales Invoice", self.recovery_invoice)
                if inv.docstatus == 1:
                    inv.cancel()
            except Exception:
                frappe.log_error(
                    frappe.get_traceback(),
                    "Fine Recovery Invoice Cancel Error"
                )
            self.db_set("recovery_invoice", None)

        self.db_set("recovery_status", "Pending")

    # ─────────────────────────────────────────
    #  RECOVERY INVOICE
    # ─────────────────────────────────────────
    def _create_recovery_invoice(self):
        if self.recovery_invoice:
            return  # idempotent

        company = (
            frappe.db.get_value("Rental Contract", self.matched_contract, "company")
            or frappe.defaults.get_global_default("company")
        )

        income_account = self._get_fine_income_account(company)
        cost_center    = frappe.get_cached_value("Company", company, "cost_center")

        _ensure_item_exists("fine_recovery", income_account, cost_center)

        si = frappe.new_doc("Sales Invoice")
        si.customer        = self.customer_at_fine_date
        si.company         = company
        si.posting_date    = today()
        si.rental_contract = self.matched_contract
        si.debit_to        = frappe.db.get_value(
            "Company", company, "default_receivable_account"
        )

        # Link back to this fine (custom field — add via Customize Form if needed)
        if hasattr(si, "traffic_fine"):
            si.traffic_fine = self.name

        si.append("items", {
            "item_code"      : FINE_ITEMS["fine_recovery"]["item_code"],
            "item_name"      : FINE_ITEMS["fine_recovery"]["item_name"],
            "description"    : (
                f"Traffic Fine Recovery | ROP Ref: {self.rop_reference_number} | "
                f"Vehicle: {self.vehicle} | Date: {self.fine_date} | "
                f"Violation: {self.violation_type}"
            ),
            "qty"            : 1,
            "rate"           : flt(self.fine_amount),
            "income_account" : income_account,
            "cost_center"    : cost_center,
        })

        si.remarks = "\n".join([
            f"Traffic Fine Recovery",
            f"ROP Reference : {self.rop_reference_number}",
            f"Vehicle       : {self.vehicle}",
            f"Fine Date     : {self.fine_date}",
            f"Violation     : {self.violation_type}",
            f"Contract      : {self.matched_contract}",
        ])

        si.set_missing_values()
        si.run_method("calculate_taxes_and_totals")
        si.insert(ignore_permissions=True)
        si.submit()

        self.db_set("recovery_invoice", si.name)
        self.db_set("recovery_status", "Invoiced")

        frappe.msgprint(
            f"✅ Recovery Invoice <b>{si.name}</b> created — "
            f"OMR {flt(self.fine_amount):,.3f} charged to "
            f"<b>{self.customer_at_fine_date}</b>.",
            title="Recovery Invoice Created",
            indicator="green"
        )

    def _get_fine_income_account(self, company):
        """
        Prefer an account with 'Fine' in its name; fall back to first income account.
        """
        fine_acc = frappe.db.get_value(
            "Account",
            {
                "company"      : company,
                "root_type"    : "Income",
                "is_group"     : 0,
                "account_name" : ("like", "%Fine%"),
            },
            "name"
        )
        if fine_acc:
            return fine_acc
        return frappe.db.get_value(
            "Account",
            {"company": company, "root_type": "Income", "is_group": 0},
            "name"
        )

    # ─────────────────────────────────────────
    #  INTERNAL GL  (Absorb Internally)
    # ─────────────────────────────────────────
    def _post_internal_gl(self):
        """
        Dr Fine Expense Account
        Cr Default Payable Account
        """
        company = (
            frappe.db.get_value("Rental Contract", self.matched_contract, "company")
            if self.matched_contract
            else frappe.defaults.get_global_default("company")
        )

        debit_account = (
            self.internal_gl_account
            or frappe.db.get_value(
                "Account",
                {
                    "company"      : company,
                    "root_type"    : "Expense",
                    "is_group"     : 0,
                    "account_name" : ("like", "%Fine%"),
                },
                "name"
            )
            or frappe.db.get_value(
                "Account",
                {"company": company, "root_type": "Expense", "is_group": 0},
                "name"
            )
        )

        credit_account = frappe.db.get_value(
            "Company", company, "default_payable_account"
        )

        if not debit_account or not credit_account:
            frappe.log_error(
                f"GL accounts not resolved for fine {self.name} | company {company}",
                "Traffic Fine GL Error"
            )
            frappe.msgprint(
                "⚠️ GL accounts could not be resolved — internal absorption "
                "journal was <b>not</b> posted. Please post manually.",
                indicator="orange"
            )
            return

        cost_center = (
            self.cost_centre
            or frappe.get_cached_value("Company", company, "cost_center")
        )

        jv = frappe.new_doc("Journal Entry")
        jv.voucher_type = "Journal Entry"
        jv.company      = company
        jv.posting_date = today()
        jv.user_remark  = (
            f"Internal absorption | Fine: {self.name} | "
            f"ROP Ref: {self.rop_reference_number} | Vehicle: {self.vehicle}"
        )

        jv.append("accounts", {
            "account"                   : debit_account,
            "debit_in_account_currency" : flt(self.fine_amount),
            "cost_center"               : cost_center,
            "reference_type"            : "Traffic Fine",
            "reference_name"            : self.name,
        })
        jv.append("accounts", {
            "account"                    : credit_account,
            "credit_in_account_currency" : flt(self.fine_amount),
            "cost_center"                : cost_center,
            "reference_type"             : "Traffic Fine",
            "reference_name"             : self.name,
        })

        jv.insert(ignore_permissions=True)
        jv.submit()

        frappe.msgprint(
            f"✅ GL posted: Journal Entry <b>{jv.name}</b> — "
            f"OMR {flt(self.fine_amount):,.3f} to Fine Expense.",
            title="GL Posted",
            indicator="blue"
        )

    # ─────────────────────────────────────────
    #  DISPUTE CREATION
    # ─────────────────────────────────────────
    def _create_dispute_doc(self):
        existing = frappe.db.get_value(
            "Fine Dispute", {"traffic_fine": self.name}, "name"
        )
        if existing:
            frappe.msgprint(
                f"Fine Dispute <b>{existing}</b> already exists for this fine.",
                indicator="orange"
            )
            return

        dispute = frappe.new_doc("Fine Dispute")
        dispute.traffic_fine      = self.name
        dispute.vehicle           = self.vehicle
        dispute.customer          = self.customer_at_fine_date
        dispute.fine_amount       = self.fine_amount
        dispute.dispute_date      = today()
        dispute.dispute_raised_by = "Internal Staff"
        dispute.dispute_status    = "Under Investigation"
        dispute.dispute_reason    = "Other"
        dispute.insert(ignore_permissions=True)

        frappe.msgprint(
            f"✅ Fine Dispute <b>{dispute.name}</b> created. "
            "Please open it to add details and assign for investigation.",
            title="Dispute Created",
            indicator="orange"
        )

    # ─────────────────────────────────────────
    #  CONTRACT FINE TOTAL (always fresh SUM)
    # ─────────────────────────────────────────
    def _update_contract_fine_total(self):
        """
        Recalculate traffic_fines_total on the matched Rental Contract.
        Uses a fresh SQL SUM so the total is always accurate regardless of
        which fines were submitted, cancelled, or added later.
        """
        if not self.matched_contract:
            return

        total = frappe.db.sql("""
            SELECT IFNULL(SUM(fine_amount), 0)
            FROM   `tabTraffic Fine`
            WHERE  matched_contract  = %s
              AND  recovery_decision = 'Charge to Customer'
              AND  docstatus         = 1
        """, self.matched_contract)[0][0] or 0

        frappe.db.set_value(
            "Rental Contract",
            self.matched_contract,
            "traffic_fines_total",
            flt(total)
        )


# ─────────────────────────────────────────────────────────────────────────────
#  CSV IMPORT API  —  called from the JS "Import ROP CSV" button
#
#  Expected CSV columns (header row required, order flexible):
#    rop_reference_number, vehicle, fine_date, fine_time,
#    violation_type, fine_amount, fine_location, rop_officer_id
# ─────────────────────────────────────────────────────────────────────────────
@frappe.whitelist()
def import_rop_csv(file_content, file_name):
    """
    Bulk-import ROP fines from a CSV string.
    All rows in one call share the same batch_id — filter list view by
    import_batch_id to review the entire import at once.

    Returns a summary dict shown to the user in JS.
    """
    import csv, io

    frappe.only_for(["Fleet Manager", "System Manager"])

    batch_id  = str(uuid.uuid4())[:12].upper()
    import_dt = today()
    importer  = frappe.session.user

    reader = csv.DictReader(io.StringIO(file_content))
    rows   = list(reader)

    total      = len(rows)
    imported   = 0
    duplicates = 0
    failed     = 0
    errors     = []

    for i, row in enumerate(rows, start=2):     # row 1 = header
        rop_ref = (row.get("rop_reference_number") or "").strip()

        if not rop_ref:
            failed += 1
            errors.append(f"Row {i}: rop_reference_number is empty — skipped")
            continue

        # ── Duplicate guard ──────────────────────────────────────────────
        if frappe.db.exists("Traffic Fine", {"rop_reference_number": rop_ref}):
            duplicates += 1
            continue

        # ── Create the Traffic Fine ──────────────────────────────────────
        try:
            tf = frappe.new_doc("Traffic Fine")
            tf.rop_reference_number = rop_ref
            tf.vehicle              = (row.get("vehicle") or "").strip()
            tf.fine_date            = (row.get("fine_date") or "").strip()
            tf.fine_time            = (row.get("fine_time") or "").strip() or None
            tf.violation_type       = (row.get("violation_type") or "Other").strip()
            tf.fine_amount          = flt(row.get("fine_amount") or 0)
            tf.fine_location        = (row.get("fine_location") or "").strip()
            tf.rop_officer_id       = (row.get("rop_officer_id") or "").strip()
            tf.recovery_decision    = "Pending Review"

            # ── Batch metadata (embedded — no separate log doctype) ──────
            tf.import_batch_id   = batch_id
            tf.file_name         = file_name
            tf.import_date       = import_dt
            tf.imported_by       = importer
            tf.import_row_status = "Imported"

            tf.insert(ignore_permissions=True)
            imported += 1

        except Exception as e:
            failed += 1
            err_msg = str(e)
            errors.append(f"Row {i} ({rop_ref}): {err_msg}")
            frappe.log_error(
                f"ROP Import row {i} failed.\nRow: {dict(row)}\nError: {err_msg}",
                "ROP CSV Import Error"
            )

    frappe.db.commit()

    # Count how many of this batch were auto-matched (post-insert validate ran)
    auto_matched = frappe.db.count(
        "Traffic Fine",
        {"import_batch_id": batch_id, "match_method": "Auto-Matched"}
    )

    return {
        "batch_id"    : batch_id,
        "total_rows"  : total,
        "imported"    : imported,
        "auto_matched": auto_matched,
        "duplicates"  : duplicates,
        "failed"      : failed,
        "errors"      : errors[:20],    # cap at 20 for display
    }
