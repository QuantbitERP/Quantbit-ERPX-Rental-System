# Copyright (c) 2026, Quantbit Technologies Pvt. Ltd.

import frappe
from frappe.model.document import Document
from frappe.utils import today, date_diff


# ─────────────────────────────────────────────────────────────────────────────
#  RENTAL ITEM CATALOGUE
#  Each entry defines the item_code, name, description and which charge field
#  on the contract maps to it.  Adding a new charge type = add one dict here.
# ─────────────────────────────────────────────────────────────────────────────
RENTAL_ITEMS = {
    "base_rental": {
        "item_code" : "Vehicle Rental",
        "item_name" : "Vehicle Rental Charge",
        "description": "Base vehicle rental charge",
    },
    "excess_km": {
        "item_code" : "Excess KM Charge",
        "item_name" : "Excess Mileage Charge",
        "description": "Charge for kilometres driven beyond the free allowance",
    },
    "late_return": {
        "item_code" : "Late Return Charge",
        "item_name" : "Late Return Penalty",
        "description": "Penalty for returning the vehicle after the agreed time",
    },
    "damage": {
        "item_code" : "Damage Charge",
        "item_name" : "Damage / Penalty Charges",
        "description": "Vehicle damage or penalty charges assessed on return",
    },
}


def _ensure_item_exists(item_key: str, income_account: str, cost_center: str):
    """
    Guarantee that the ERPNext Item record for *item_key* exists.

    • If it already exists  → nothing to do.
    • If it is missing      → create it automatically as a non-stock Service item
                              so invoice insertion never fails due to a missing item.

    This is intentionally idempotent: safe to call every time an invoice is built.
    """
    meta = RENTAL_ITEMS[item_key]
    code = meta["item_code"]

    if frappe.db.exists("Item", code):
        return  # already present — fast path

    frappe.logger().info(
        f"[RentalContract] Auto-creating missing Item '{code}'"
    )

    item = frappe.new_doc("Item")
    item.item_code        = code
    item.item_name        = meta["item_name"]
    item.description      = meta["description"]
    item.item_group       = "Services"          # adjust to your Item Group if different
    item.stock_uom        = "Nos"
    item.is_stock_item    = 0
    item.is_sales_item    = 1
    item.is_purchase_item = 0

    # Default income account so the item can be used on invoices immediately
    if income_account:
        item.append("item_defaults", {
            "company"        : frappe.defaults.get_global_default("company"),
            "income_account" : income_account,
            "cost_center"    : cost_center,
        })

    item.insert(ignore_permissions=True)
    frappe.db.commit()


class RentalContract(Document):

    # ─────────────────────────────────────────────
    #  VALIDATE  (runs on every save / update)
    # ─────────────────────────────────────────────
    def validate(self):
        self.set_customer_kyc()
        self.set_vehicle_status_snapshot()
        self.set_rate_from_card()

        self.validate_customer_kyc()
        self.validate_licence()
        self.validate_vehicle()
        self.validate_dates()
        self.validate_km()
        self.validate_rate_card()

        self.set_total_days()
        self.set_km_used()
        self.calculate_charges()

        self.update_contract_status()

        self.set_flags()
        self.set_rate_type()

    # ─────────────────────────────────────────────
    #  BEFORE SUBMIT  — handover checklist gate
    # ─────────────────────────────────────────────
    def before_submit(self):
        """
        Gate: Pre-Delivery Handover Checklist must be submitted
        before this contract can be submitted.
        The link is written automatically by handover_checklist.on_submit —
        staff never needs to paste anything manually.
        """
        if not self.handover_checklist:
            frappe.throw(
                "⛔ Submission blocked: The Pre-Delivery Handover Checklist "
                "has not been completed yet.<br><br>"
                "Steps to fix:<br>"
                "1. Open <b>Handover Checklist → New</b><br>"
                "2. Select this contract in the Rental Contract field<br>"
                "3. Fill and <b>Submit</b> the checklist<br>"
                "4. Come back here and submit the contract",
                title="Handover Checklist Required"
            )

        checklist_status = frappe.db.get_value(
            "Handover Checklist", self.handover_checklist, "docstatus"
        )

        if checklist_status is None:
            frappe.throw(
                "⛔ The linked Handover Checklist record was not found. "
                "Please re-check the ERPNext References section.",
                title="Invalid Checklist"
            )

        if checklist_status != 1:
            frappe.throw(
                "⛔ The linked Handover Checklist is saved but not yet submitted. "
                "Please open it and submit it first.",
                title="Checklist Not Submitted"
            )

    # ─────────────────────────────────────────────
    #  AFTER INSERT
    # ─────────────────────────────────────────────
    def after_insert(self):
        frappe.msgprint(
            "⚠️ Please fill the Pre-Delivery Handover Checklist, then link it "
            "here and submit the contract. Submission will be blocked without it.",
            title="Action Required"
        )

    # ─────────────────────────────────────────────
    #  ON SUBMIT  — mark vehicle On Rent
    # ─────────────────────────────────────────────
    def on_submit(self):
        if self.contract_status == "Active":
            frappe.db.set_value("Vehicle Master", self.vehicle, {
                "vehicle_status"   : "On Rent",
                "current_contract" : self.name,
            })

    # ─────────────────────────────────────────────
    #  ON UPDATE AFTER SUBMIT  — closure flow
    # ─────────────────────────────────────────────
    def on_update_after_submit(self):
        self.update_contract_status()
        self.db_set("contract_status", self.contract_status)

        if self.contract_status == "Pending Return":
            frappe.msgprint(
                "Vehicle returned physically. "
                "Please complete the Post-Return Checklist to fully close the contract."
            )
            return

        if self.contract_status != "Closed":
            return

        # ── Release vehicle ──
        frappe.db.set_value("Vehicle Master", self.vehicle, {
            "vehicle_status"   : "Available",
            "current_contract" : None,
        })

        # ── Fetch totals from child payment docs ──
        total_advance = frappe.db.sql("""
            SELECT IFNULL(SUM(advance_amount), 0)
            FROM   `tabAdvance Payment Entry`
            WHERE  rental_contract = %s AND docstatus = 1
        """, self.name)[0][0] or 0

        total_deposit = frappe.db.sql("""
            SELECT IFNULL(SUM(deposit_amount), 0)
            FROM   `tabSecurity Deposit Entry`
            WHERE  rental_contract = %s AND docstatus = 1
        """, self.name)[0][0] or 0

        damage = self.damage_charges or 0
        mode   = self.deposit_settlement_mode or ""

        # ── Deposit settlement logic ──
        deposit_used_for_damage = min(damage, total_deposit)
        deposit_refund          = total_deposit - deposit_used_for_damage

        if mode == "Apply to Final Invoice First":
            deposit_applied_to_invoice = min(
                deposit_refund,
                (self.total_amount or 0) - total_advance
            )
            deposit_applied_to_invoice = max(deposit_applied_to_invoice, 0)
            deposit_refund -= deposit_applied_to_invoice
        else:
            deposit_applied_to_invoice = 0

        net_due = (self.total_amount or 0) - total_advance - deposit_applied_to_invoice
        net_due = max(net_due, 0)

        # ── Persist summary fields ──
        self.db_set("advance_applied", total_advance)
        self.db_set("deposit_applied", deposit_applied_to_invoice)
        self.db_set("net_due",         net_due)

        # ── Create invoice if not already done ──
        if not self.sales_invoice:
            self.create_sales_invoice(
                total_advance           = total_advance,
                total_deposit           = total_deposit,
                damage_charges          = damage,
                deposit_used_for_damage = deposit_used_for_damage,
                deposit_refund          = deposit_refund,
                deposit_applied_invoice = deposit_applied_to_invoice,
                net_due                 = net_due,
                mode                    = mode,
            )

    # ─────────────────────────────────────────────
    #  SALES INVOICE CREATION
    # ─────────────────────────────────────────────
    def create_sales_invoice(
        self,
        total_advance,
        total_deposit,
        damage_charges,
        deposit_used_for_damage,
        deposit_refund,
        deposit_applied_invoice,
        net_due,
        mode,
    ):
        """
        Creates a Sales Invoice with:
          • Line 1 : Base rental  (rate × days)
          • Line 2 : Excess KM charges  (if any)
          • Line 3 : Late return charge  (if any)
          • Line 4 : Damage / penalty charges  (if any)

        All required Item records are auto-created on first use so this
        method never fails with "Item X not found".
        """
        try:
            # ── Resolve accounts ──────────────────────────────────────────
            income_account = frappe.db.get_value(
                "Account",
                {"company": self.company, "root_type": "Income", "is_group": 0},
                "name"
            )
            cost_center = frappe.get_cached_value(
                "Company", self.company, "cost_center"
            )

            # ── Guarantee every Item exists before touching the invoice ───
            for key in RENTAL_ITEMS:
                _ensure_item_exists(key, income_account, cost_center)

            # ── Build the Sales Invoice ───────────────────────────────────
            si = frappe.new_doc("Sales Invoice")
            si.customer     = self.customer
            si.company      = self.company
            si.posting_date = today()
            si.rental_contract = self.name
            si.debit_to     = frappe.db.get_value(
                "Company", self.company, "default_receivable_account"
            )

            # ── Line 1: Base Rental ───────────────────────────────────────
            si.append("items", {
                "item_code"      : RENTAL_ITEMS["base_rental"]["item_code"],
                "item_name"      : RENTAL_ITEMS["base_rental"]["item_name"],
                "description"    : (
                    f"Rental: {self.vehicle} | "
                    f"{self.date_out} to {self.actual_return_date or self.date_return} "
                    f"({self.total_days or 0} day(s)) "
                    f"@ {self.rate or 0} OMR/{self.contract_type}"
                ),
                "qty"            : self.total_days or 1,
                "rate"           : self.rate or 0,
                "income_account" : income_account,
                "cost_center"    : cost_center,
            })

            # ── Line 2: Excess KM ─────────────────────────────────────────
            if (self.excess_km_charges or 0) > 0:
                si.append("items", {
                    "item_code"      : RENTAL_ITEMS["excess_km"]["item_code"],
                    "item_name"      : RENTAL_ITEMS["excess_km"]["item_name"],
                    "description"    : (
                        f"Excess KM: {self.km_used or 0} km used — "
                        "free allowance exceeded, charged per contract rate"
                    ),
                    "qty"            : 1,
                    "rate"           : self.excess_km_charges or 0,
                    "income_account" : income_account,
                    "cost_center"    : cost_center,
                })

            # ── Line 3: Late Return ───────────────────────────────────────
            if (self.late_return_charge or 0) > 0:
                si.append("items", {
                    "item_code"      : RENTAL_ITEMS["late_return"]["item_code"],
                    "item_name"      : RENTAL_ITEMS["late_return"]["item_name"],
                    "description"    : "Late return charge as per contract terms",
                    "qty"            : 1,
                    "rate"           : self.late_return_charge or 0,
                    "income_account" : income_account,
                    "cost_center"    : cost_center,
                })

            # ── Line 4: Damage / Penalties ────────────────────────────────
            if damage_charges > 0:
                si.append("items", {
                    "item_code"      : RENTAL_ITEMS["damage"]["item_code"],
                    "item_name"      : RENTAL_ITEMS["damage"]["item_name"],
                    "description"    : "Vehicle damage or penalty charges assessed on return",
                    "qty"            : 1,
                    "rate"           : damage_charges,
                    "income_account" : income_account,
                    "cost_center"    : cost_center,
                })

            # ── Compute ERPNext totals ─────────────────────────────────────
            si.set_missing_values()
            si.run_method("calculate_taxes_and_totals")

            grand_total = si.grand_total or 0

            # ── Settlement summary (custom fields on SI — display only) ───
            _set_if_exists = lambda doc, field, val: (
                setattr(doc, field, val) if hasattr(doc, field) else None
            )
            _set_if_exists(si, "advance_applied",            total_advance)
            _set_if_exists(si, "deposit_collected",          total_deposit)
            _set_if_exists(si, "deposit_used_for_damage",    deposit_used_for_damage)
            _set_if_exists(si, "deposit_applied_to_invoice", deposit_applied_invoice)
            _set_if_exists(si, "deposit_refund_due",         deposit_refund)
            _set_if_exists(si, "net_amount_due",             net_due)

            # ── Remarks (always visible on printed invoice) ───────────────
            remarks_lines = [
                f"Rental Contract : {self.name}",
                f"Vehicle          : {self.vehicle}",
                f"Period           : {self.date_out} → {self.actual_return_date or self.date_return}",
                "─" * 40,
                f"Invoice Total    : OMR {grand_total:,.3f}",
                f"Advance Paid     : OMR {total_advance:,.3f}  (deducted)",
            ]
            if deposit_used_for_damage > 0:
                remarks_lines.append(
                    f"Deposit → Damage : OMR {deposit_used_for_damage:,.3f}  (applied)"
                )
            if deposit_applied_invoice > 0:
                remarks_lines.append(
                    f"Deposit → Invoice: OMR {deposit_applied_invoice:,.3f}  (applied)"
                )
            if deposit_refund > 0:
                remarks_lines.append(
                    f"Deposit Refund   : OMR {deposit_refund:,.3f}  ← DUE TO CUSTOMER"
                )
            remarks_lines += [
                "─" * 40,
                f"NET DUE FROM CUSTOMER: OMR {net_due:,.3f}",
            ]
            if net_due == 0 and deposit_refund > 0:
                remarks_lines.append(
                    f"⚠️  Please process refund of OMR {deposit_refund:,.3f} to customer."
                )
            si.remarks = "\n".join(remarks_lines)

            # ── Save & submit ─────────────────────────────────────────────
            si.insert(ignore_permissions=True)
            si.submit()

            self.db_set("sales_invoice", si.name)

            # ── Notify staff ──────────────────────────────────────────────
            if deposit_refund > 0:
                frappe.msgprint(
                    f"✅ Sales Invoice <b>{si.name}</b> created.<br><br>"
                    f"⚠️ <b>Deposit Refund Due: OMR {deposit_refund:,.3f}</b><br>"
                    "Please process the refund to the customer via the appropriate payment method.",
                    title="Invoice Created — Refund Required",
                    indicator="orange"
                )
            else:
                frappe.msgprint(
                    f"✅ Sales Invoice <b>{si.name}</b> created successfully.<br>"
                    f"Net Amount Due from Customer: <b>OMR {net_due:,.3f}</b>",
                    title="Invoice Created",
                    indicator="green"
                )

        except frappe.ValidationError:
            # Re-raise Frappe validation errors as-is (they already have a user-friendly message)
            raise

        except Exception:
            frappe.log_error(frappe.get_traceback(), "Rental Invoice Creation Error")
            frappe.throw(
                "Invoice creation failed. Please check the Error Log for details.",
                title="Invoice Error"
            )

    # ─────────────────────────────────────────────────────────────
    #  SETTERS
    # ─────────────────────────────────────────────────────────────
    def set_customer_kyc(self):
        if self.customer and not self.customer_kyc:
            self.customer_kyc = frappe.db.get_value(
                "Customer KYC", {"customer": self.customer}, "name"
            )

    def set_vehicle_status_snapshot(self):
        if self.vehicle:
            self.vehicle_status_at_contract = frappe.db.get_value(
                "Vehicle Master", self.vehicle, "vehicle_status"
            )

    def set_rate_from_card(self):
        if not self.contract_type or not self.rate_card:
            return
        rc = frappe.get_doc("Rate Card", self.rate_card)
        if self.contract_type == "Daily":
            self.rate = rc.daily_rate or 0
        elif self.contract_type == "Weekly":
            self.rate = rc.weekly_rate or 0
        elif self.contract_type == "Monthly":
            self.rate = rc.monthly_rate or 0

    def set_total_days(self):
        if self.date_out:
            end_date = self.actual_return_date or self.date_return
            if end_date:
                self.total_days = date_diff(end_date, self.date_out) + 1

    def set_km_used(self):
        if self.km_out is not None and self.km_return is not None:
            self.km_used = self.km_return - self.km_out

    def set_flags(self):
        self.is_active = 1 if self.contract_status == "Active" else 0
        self.is_closed = 1 if self.contract_status == "Closed" else 0

    def set_rate_type(self):
        self.rate_type_used = self.contract_type

    # ─────────────────────────────────────────────────────────────
    #  VALIDATORS  (stubs — fill in your business rules)
    # ─────────────────────────────────────────────────────────────
    def validate_customer_kyc(self): pass
    def validate_licence(self):      pass
    def validate_vehicle(self):      pass
    def validate_dates(self):        pass
    def validate_km(self):           pass
    def validate_rate_card(self):    pass

    # ─────────────────────────────────────────────────────────────
    #  CALCULATIONS
    # ─────────────────────────────────────────────────────────────
    def calculate_charges(self):
        rate       = self.rate or 0
        total_days = self.total_days or 0

        self.base_rental_amount = rate * total_days
        self.excess_km_charges  = 0

        if self.contract_type == "Daily":
            free_km       = (self.free_km_per_day or 0) * total_days
            extra_km_rate = self.excess_km_charge_daily or 0
        elif self.contract_type == "Weekly":
            weeks         = total_days // 7
            free_km       = (self.free_km_per_week or 0) * weeks
            extra_km_rate = self.excess_km_charge_daily or 0
        elif self.contract_type == "Monthly":
            months        = total_days // 30
            free_km       = (self.free_km_per_month or 0) * months
            extra_km_rate = self.excess_km_charge_monthly or 0
        else:
            free_km       = 0
            extra_km_rate = 0

        if self.km_used is not None and self.km_used > free_km:
            self.excess_km_charges = (self.km_used - free_km) * extra_km_rate

        self.gross_amount = self.base_rental_amount + self.excess_km_charges
        vat               = self.get_vat_rate()
        self.vat_amount   = (self.gross_amount * vat) / 100
        self.total_amount = self.gross_amount + self.vat_amount

    def update_contract_status(self):
        if self.contract_status == "Cancelled":
            return
        if self.actual_return_date and self.km_return is not None:
            self.contract_status = "Closed"
        elif self.actual_return_date:
            self.contract_status = "Pending Return"
        else:
            self.contract_status = "Active"

    # ─────────────────────────────────────────────────────────────
    #  HELPERS
    # ─────────────────────────────────────────────────────────────
    def get_vat_rate(self):
        """
        Tries VAT Configuration table first.
        Falls back to parsing the vat_rate select field on this doc
        (e.g. '5% — Oman'  →  5.0).
        """
        vat = frappe.db.get_value(
            "VAT Configuration",
            {"company": self.company, "is_active": 1},
            "vat_rate"
        )
        if vat:
            return vat

        vat_label = self.vat_rate or ""
        if vat_label.startswith("5"):
            return 5.0
        elif vat_label.startswith("15"):
            return 15.0
        return 0.0