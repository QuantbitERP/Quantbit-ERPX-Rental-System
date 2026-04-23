# Copyright (c) 2026, Quantbit Technologies Pvt. Ltd.

import frappe
from frappe.model.document import Document
from frappe.utils import today, date_diff, flt, time_diff_in_hours


RENTAL_ITEMS = {
    "base_rental":       {"item_code": "Vehicle Rental",           "item_name": "Vehicle Rental Charge",              "description": "Base vehicle rental charge"},
    "excess_km":         {"item_code": "Excess KM Charge",         "item_name": "Excess Mileage Charge",              "description": "Charge for kilometres driven beyond the free allowance"},
    "late_return":       {"item_code": "Late Return Charge",        "item_name": "Late Return Penalty",                "description": "Penalty for returning the vehicle after the agreed time"},
    "damage":            {"item_code": "Damage Charge",             "item_name": "Damage / Penalty Charges",           "description": "Vehicle damage or penalty charges assessed on return"},
    "missing_accessory": {"item_code": "Missing Accessory Charge",  "item_name": "Missing Accessory Charge",           "description": "Charge for accessories missing on vehicle return"},
    "traffic_fine_ref":  {"item_code": "Traffic Fine Recovery",     "item_name": "Traffic Fine Recovery (Reference)",  "description": "Reference line — fine recovery billed via separate invoice"},
}


def _ensure_item_exists(item_key: str, income_account: str, cost_center: str):
    meta = RENTAL_ITEMS[item_key]
    code = meta["item_code"]
    if frappe.db.exists("Item", code):
        return
    frappe.logger().info(f"[RentalContract] Auto-creating missing Item '{code}'")
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
            "company":        frappe.defaults.get_global_default("company"),
            "income_account": income_account,
            "cost_center":    cost_center,
        })
    item.insert(ignore_permissions=True)
    frappe.db.commit()


class RentalContract(Document):

    def validate(self):
        self.set_customer_kyc()
        self.set_customer_details()
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
        self.sync_checklist_charges()
        self.sync_fine_summary()           # rebuilds fine_summary_text, fine_summary_html, total_fines_on_contract
        self._set_live_billing_summary()   # sets advance_applied, deposit_applied, net_due from JV or field fallback
        self.set_flags()
        self.set_rate_type()

    def before_submit(self):
        if not self.handover_checklist:
            frappe.throw(
                "⛔ Submission blocked: Pre-Delivery Handover Checklist not completed.<br><br>"
                "<b>Steps:</b><br>"
                "1. Open <b>Handover Checklist → New</b><br>"
                "2. Select this contract, type = <b>Pre-Delivery (Handover)</b><br>"
                "3. Fill all fields and <b>Submit</b> the checklist<br>"
                "4. Return here and submit the contract",
                title="Pre-Delivery Checklist Required",
            )
        checklist_status = frappe.db.get_value("Handover Checklist", self.handover_checklist, "docstatus")
        if checklist_status is None:
            frappe.throw("⛔ The linked Handover Checklist record was not found.", title="Invalid Checklist")
        if checklist_status != 1:
            frappe.throw("⛔ The linked Handover Checklist is saved but not yet submitted.", title="Checklist Not Submitted")

    def after_insert(self):
        frappe.msgprint(
            "⚠️ Next step: Fill the <b>Pre-Delivery Handover Checklist</b> for this contract and submit it before submitting this contract.",
            title="Action Required — Pre-Delivery Checklist",
        )

    def on_submit(self):
        if self.contract_status == "Active":
            frappe.db.set_value("Vehicle Master", self.vehicle, {
                "vehicle_status":   "On Rent",
                "current_contract": self.name,
            })

    def on_update_after_submit(self):
        self.update_contract_status()
        self.sync_checklist_charges()
        self.sync_fine_summary()
        self.db_set("contract_status", self.contract_status)

        if self.contract_status == "Pending Return":
            return
        if self.contract_status != "Closed":
            return

        self._gate_post_return_checklist()
        self._gate_pending_fines()

        frappe.db.set_value("Vehicle Master", self.vehicle, {
            "vehicle_status":   "Available",
            "current_contract": None,
        })

        # Read actual paid amounts from submitted JVs (falls back to contract fields if no JV)
        total_advance = self._get_jv_total("advance_journal_entry", "advance_amount")
        total_deposit = self._get_jv_total("deposit_journal_entry", "security_deposit")

        # total_fines_on_contract is already set by sync_fine_summary; read it back
        fines_total = flt(self.total_fines_on_contract or 0)

        self.db_set("traffic_fines_total", fines_total)   # keep field in sync (same value, kept for legacy)

        damage                  = flt(self.damage_charges or 0)
        mode                    = self.deposit_settlement_mode or ""
        deposit_used_for_damage = min(damage, total_deposit)
        deposit_refund          = total_deposit - deposit_used_for_damage

        if mode == "Apply to Final Invoice First":
            deposit_applied_to_invoice = max(min(deposit_refund, flt(self.total_amount or 0) - total_advance), 0)
            deposit_refund            -= deposit_applied_to_invoice
        else:
            deposit_applied_to_invoice = 0

        net_due = max(flt(self.total_amount or 0) - total_advance - deposit_applied_to_invoice, 0)

        self.db_set("advance_applied", total_advance)
        self.db_set("deposit_applied", deposit_applied_to_invoice)
        self.db_set("net_due",         net_due)

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
                fines_total             = fines_total,
            )

    # ── JV amount reader — uses submitted JV credit total, falls back to contract field ──

    def _get_jv_total(self, jv_link_field: str, fallback_field: str) -> float:
        jv_name = self.get(jv_link_field)
        if jv_name:
            jv_status = frappe.db.get_value("Journal Entry", jv_name, "docstatus")
            if jv_status == 1:
                total = frappe.db.sql(
                    "SELECT IFNULL(SUM(credit_in_account_currency),0) FROM `tabJournal Entry Account` WHERE parent=%s",
                    jv_name,
                )[0][0]
                return flt(total)
        return flt(self.get(fallback_field) or 0)

    # ── Gate 1: Post-Return Checklist must be submitted ──

    def _gate_post_return_checklist(self):
        if not self.return_checklist:
            frappe.throw(
                "⛔ Contract cannot close yet — <b>Post-Return Checklist</b> not done.<br><br>"
                "<b>Steps:</b><br>"
                "1. Open <b>Handover Checklist → New</b><br>"
                "2. Select this contract, type = <b>Post-Return</b><br>"
                "3. Fill all panels and accessories, then <b>Submit</b><br>"
                "4. Return here and save — contract will close automatically",
                title="Post-Return Checklist Required",
            )
        checklist_status = frappe.db.get_value("Handover Checklist", self.return_checklist, "docstatus")
        if checklist_status != 1:
            frappe.throw("⛔ The linked Post-Return Checklist is saved but not yet submitted.", title="Post-Return Checklist Not Submitted")

    # ── Gate 2: All traffic fines must be resolved before invoice ──

    def _gate_pending_fines(self):
        pending_review = frappe.db.sql("""
            SELECT name, rop_reference_number, fine_amount, violation_type
            FROM `tabTraffic Fine`
            WHERE matched_contract=%s AND docstatus=1 AND recovery_decision='Pending Review'
        """, self.name, as_dict=True)

        active_dispute = frappe.db.sql("""
            SELECT tf.name, tf.rop_reference_number, tf.fine_amount,
                   fd.name AS dispute_name, fd.dispute_status
            FROM `tabTraffic Fine` tf
            JOIN `tabFine Dispute` fd ON fd.traffic_fine=tf.name
            WHERE tf.matched_contract=%s AND tf.docstatus=1
              AND fd.docstatus<2 AND fd.dispute_status IN ('Under Investigation','Escalated to ROP')
        """, self.name, as_dict=True)

        if not pending_review and not active_dispute:
            return

        lines = ["⛔ <b>Sales Invoice cannot be generated</b> — the following Traffic Fines are not yet resolved:<br><br>"]
        if pending_review:
            lines.append("<b>Awaiting Recovery Decision:</b><ul>")
            for f in pending_review:
                lines.append(f"<li>ROP Ref: <b>{f.rop_reference_number}</b> | {f.violation_type} | OMR {flt(f.fine_amount):,.3f} — <a href='/app/traffic-fine/{f.name}'>Open Fine →</a></li>")
            lines.append("</ul>")
        if active_dispute:
            lines.append("<b>Unresolved Disputes:</b><ul>")
            for f in active_dispute:
                lines.append(f"<li>ROP Ref: <b>{f.rop_reference_number}</b> | OMR {flt(f.fine_amount):,.3f} — Dispute <b>{f.dispute_name}</b> ({f.dispute_status}) — <a href='/app/fine-dispute/{f.dispute_name}'>Resolve →</a></li>")
            lines.append("</ul>")
        lines.append("<br>Resolve all outstanding fines, then save the contract again.")
        frappe.throw("".join(lines), title="Unresolved Traffic Fines")

    # ── Pull + persist checklist data into contract fields ──

    def sync_checklist_charges(self):
        if not self.return_checklist:
            self.checklist_missing_items              = ""
            self.checklist_missing_item_count         = 0
            self.checklist_missing_accessories_charge = 0
            self.new_damage_panels                    = ""
            return

        cl = frappe.db.get_value(
            "Handover Checklist", self.return_checklist,
            ["missing_accessories", "missing_item_count", "missing_accessories_charge", "new_damage_panels"],
            as_dict=True,
        )
        if not cl:
            return

        self.checklist_missing_items              = cl.get("missing_accessories") or ""
        self.checklist_missing_item_count         = int(cl.get("missing_item_count") or 0)
        self.checklist_missing_accessories_charge = flt(cl.get("missing_accessories_charge") or 0)
        self.new_damage_panels                    = cl.get("new_damage_panels") or ""

        if self.docstatus == 1:
            frappe.db.set_value(self.doctype, self.name, {
                "checklist_missing_items":              self.checklist_missing_items,
                "checklist_missing_item_count":         self.checklist_missing_item_count,
                "checklist_missing_accessories_charge": self.checklist_missing_accessories_charge,
                "new_damage_panels":                    self.new_damage_panels,
            })

    # ── Rebuild fine summary text + HTML + total_fines_on_contract ──
    # NOTE: total_fines_on_contract = sum of fines where recovery_decision='Charge to Customer'
    # NOTE: traffic_fines_total is kept as a mirror field (same value) for legacy/report compatibility

    def sync_fine_summary(self):
        if not self.name or self.name == "new-rental-contract-1":
            return

        fines = frappe.db.sql("""
            SELECT tf.name, tf.rop_reference_number, tf.fine_date, tf.violation_type,
                   tf.fine_amount, tf.recovery_decision, tf.recovery_status, tf.recovery_invoice,
                   fd.name AS dispute_name, fd.dispute_status AS dispute_status
            FROM `tabTraffic Fine` tf
            LEFT JOIN `tabFine Dispute` fd ON fd.traffic_fine=tf.name AND fd.docstatus<2
            WHERE tf.matched_contract=%s AND tf.docstatus=1
            ORDER BY tf.fine_date
        """, self.name, as_dict=True)

        # Compute total charged to customer and persist to total_fines_on_contract
        charged_total = sum(flt(f.fine_amount) for f in fines if f.recovery_decision == "Charge to Customer")
        self.total_fines_on_contract = charged_total
        if self.docstatus == 1:
            try:
                frappe.db.set_value(self.doctype, self.name, "total_fines_on_contract", charged_total)
            except Exception:
                pass

        if not fines:
            text = "✅ No traffic fines on this contract."
            html = "<p style='color:green;font-size:13px'>✅ No traffic fines on this contract.</p>"
            self.fine_summary_text = text
            self.fine_summary_html = html
            if self.docstatus == 1:
                try:
                    frappe.db.set_value(self.doctype, self.name, "fine_summary_text", text)
                except Exception:
                    pass
                try:
                    frappe.db.set_value(self.doctype, self.name, "fine_summary_html", html)
                except Exception:
                    pass
            return

        charged  = [f for f in fines if f.recovery_decision == "Charge to Customer"]
        absorbed = [f for f in fines if f.recovery_decision == "Absorb Internally"]
        disputed = [f for f in fines if f.recovery_decision == "Under Dispute"]
        pending  = [f for f in fines if f.recovery_decision == "Pending Review"]

        total_absorbed = sum(flt(f.fine_amount) for f in absorbed)
        total_disputed = sum(flt(f.fine_amount) for f in disputed)
        total_pending  = sum(flt(f.fine_amount) for f in pending)
        grand_total    = sum(flt(f.fine_amount) for f in fines)

        SEP   = "─" * 54
        lines = []

        def _text_group(fine_list, label):
            if not fine_list:
                return
            total = sum(flt(f.fine_amount) for f in fine_list)
            lines.append(f"\n{label} ({len(fine_list)} fine(s) — OMR {total:,.3f})")
            lines.append(SEP)
            for f in fine_list:
                dispute_info = f" | Dispute: {f.dispute_name} [{f.dispute_status}]" if f.dispute_name else ""
                invoice_info = f" | Invoice: {f.recovery_invoice}" if f.recovery_invoice else ""
                lines.append(
                    f"  • {f.fine_date}  {f.rop_reference_number:<14} "
                    f"{f.violation_type:<22} OMR {flt(f.fine_amount):>8,.3f}"
                    f"  [{f.recovery_status or '—'}]{invoice_info}{dispute_info}"
                )

        if charged:  _text_group(charged,  "✅ CHARGED TO CUSTOMER")
        if absorbed: _text_group(absorbed, "🔵 ABSORBED INTERNALLY")
        if disputed: _text_group(disputed, "🟠 UNDER DISPUTE")
        if pending:  _text_group(pending,  "🔴 ⚠️  PENDING REVIEW — ACTION NEEDED")

        lines.append(f"\n{SEP}")
        lines.append(f"  Grand Total : OMR {grand_total:,.3f}  ({len(fines)} fine(s))")
        if pending:
            lines.append(f"  ⚠️  Unresolved (Pending Review): OMR {total_pending:,.3f}")

        text = "\n".join(lines).strip()

        # Build HTML table
        rows_html = []

        def _html_rows(fine_list, badge_color, badge_text):
            for f in fine_list:
                inv_link     = f"<a href='/app/sales-invoice/{f.recovery_invoice}' target='_blank'>{f.recovery_invoice}</a>" if f.recovery_invoice else "—"
                dispute_link = f"<a href='/app/fine-dispute/{f.dispute_name}' target='_blank'>{f.dispute_name} ({f.dispute_status})</a>" if f.dispute_name else "—"
                rows_html.append(
                    f"<tr>"
                    f"<td><a href='/app/traffic-fine/{f.name}' target='_blank'>{f.rop_reference_number}</a></td>"
                    f"<td>{f.fine_date}</td><td>{f.violation_type}</td>"
                    f"<td style='text-align:right'>OMR {flt(f.fine_amount):,.3f}</td>"
                    f"<td><span style='background:{badge_color};color:#fff;padding:2px 8px;border-radius:4px;font-size:11px'>{badge_text}</span></td>"
                    f"<td>{inv_link}</td><td>{dispute_link}</td>"
                    f"</tr>"
                )

        _html_rows(charged,  "#2e7d32", "Charged to Customer")
        _html_rows(absorbed, "#1565c0", "Absorbed Internally")
        _html_rows(disputed, "#e65100", "Under Dispute")
        _html_rows(pending,  "#b71c1c", "⚠️ Pending Review")

        summary_parts = []
        if charged:  summary_parts.append(f"<b style='color:#2e7d32'>Charged:</b> OMR {charged_total:,.3f} ({len(charged)})")
        if absorbed: summary_parts.append(f"<b style='color:#1565c0'>Absorbed:</b> OMR {total_absorbed:,.3f} ({len(absorbed)})")
        if disputed: summary_parts.append(f"<b style='color:#e65100'>Disputed:</b> OMR {total_disputed:,.3f} ({len(disputed)})")
        if pending:  summary_parts.append(f"<b style='color:#b71c1c'>⚠️ Pending:</b> OMR {total_pending:,.3f} ({len(pending)})")

        html = "".join([
            "<div style='font-size:13px'>",
            "<table style='width:100%;border-collapse:collapse' class='table table-bordered table-condensed'>",
            "<thead><tr style='background:#f5f5f5'>",
            "<th>ROP Ref</th><th>Date</th><th>Violation</th><th>Amount</th><th>Status</th><th>Recovery Invoice</th><th>Dispute</th>",
            "</tr></thead><tbody>",
            *rows_html,
            "</tbody></table>",
            "<div style='margin-top:8px;line-height:1.8'>",
            " &nbsp;|&nbsp; ".join(summary_parts),
            f"<br><b>Grand Total: OMR {grand_total:,.3f}</b>",
            "</div></div>",
        ])

        self.fine_summary_text = text
        self.fine_summary_html = html

        if self.docstatus == 1:
            try:
                frappe.db.set_value(self.doctype, self.name, "fine_summary_text", text)
            except Exception:
                pass
            try:
                frappe.db.set_value(self.doctype, self.name, "fine_summary_html", html)
            except Exception:
                pass

    # ── Live billing summary shown on form during draft/active state ──
    # Uses actual JV amounts if submitted JVs are linked, else falls back to contract fields

    def _set_live_billing_summary(self):
        advance = self._get_jv_total("advance_journal_entry", "advance_amount")
        deposit = self._get_jv_total("deposit_journal_entry", "security_deposit")
        total   = flt(self.total_amount or 0)
        damage  = flt(self.damage_charges or 0)
        mode    = self.deposit_settlement_mode or ""

        deposit_used_for_damage    = min(damage, deposit)
        deposit_refund             = deposit - deposit_used_for_damage
        deposit_applied_to_invoice = 0

        if mode == "Apply to Final Invoice First":
            deposit_applied_to_invoice = max(min(deposit_refund, total - advance), 0)
            deposit_refund            -= deposit_applied_to_invoice

        self.advance_applied = advance
        self.deposit_applied = deposit_applied_to_invoice
        self.net_due         = max(total - advance - deposit_applied_to_invoice, 0)

    # ── Create closing Sales Invoice on contract closure ──

    def create_sales_invoice(
        self, total_advance, total_deposit, damage_charges,
        deposit_used_for_damage, deposit_refund, deposit_applied_invoice,
        net_due, mode, fines_total=0,
    ):
        try:
            income_account = self._get_income_account()
            cost_center    = frappe.get_cached_value("Company", self.company, "cost_center")

            for key in RENTAL_ITEMS:
                _ensure_item_exists(key, income_account, cost_center)

            si = frappe.new_doc("Sales Invoice")
            si.customer        = self.customer
            si.company         = self.company
            si.posting_date    = today()
            si.rental_contract = self.name
            si.debit_to        = frappe.db.get_value("Company", self.company, "default_receivable_account")

            si.append("items", {
                "item_code":      RENTAL_ITEMS["base_rental"]["item_code"],
                "item_name":      RENTAL_ITEMS["base_rental"]["item_name"],
                "description":    f"Vehicle Rental: {self.vehicle} | {self.date_out} → {self.actual_return_date or self.date_return} ({self.total_days or 0} day(s)) @ OMR {flt(self.rate or 0):,.3f} / {self.contract_type or 'Day'}",
                "qty":            self.total_days or 1,
                "rate":           flt(self.rate or 0),
                "income_account": income_account,
                "cost_center":    cost_center,
            })

            if flt(self.excess_km_charges or 0) > 0:
                si.append("items", {
                    "item_code":      RENTAL_ITEMS["excess_km"]["item_code"],
                    "item_name":      RENTAL_ITEMS["excess_km"]["item_name"],
                    "description":    f"Excess KM: {flt(self.km_used or 0):,.0f} km used — free allowance exceeded",
                    "qty":            1,
                    "rate":           flt(self.excess_km_charges),
                    "income_account": income_account,
                    "cost_center":    cost_center,
                })

            if flt(self.late_return_charge or 0) > 0:
                si.append("items", {
                    "item_code":      RENTAL_ITEMS["late_return"]["item_code"],
                    "item_name":      RENTAL_ITEMS["late_return"]["item_name"],
                    "description":    f"Late return — {flt(self.late_return_days or 0):,.1f} day(s) overdue",
                    "qty":            1,
                    "rate":           flt(self.late_return_charge),
                    "income_account": income_account,
                    "cost_center":    cost_center,
                })

            if damage_charges > 0:
                si.append("items", {
                    "item_code":      RENTAL_ITEMS["damage"]["item_code"],
                    "item_name":      RENTAL_ITEMS["damage"]["item_name"],
                    "description":    "Vehicle damage or penalty charges assessed on return",
                    "qty":            1,
                    "rate":           flt(damage_charges),
                    "income_account": income_account,
                    "cost_center":    cost_center,
                })

            missing_acc_charge = flt(self.checklist_missing_accessories_charge or 0)
            if missing_acc_charge > 0:
                si.append("items", {
                    "item_code":      RENTAL_ITEMS["missing_accessory"]["item_code"],
                    "item_name":      RENTAL_ITEMS["missing_accessory"]["item_name"],
                    "description":    f"Missing accessories on return ({int(self.checklist_missing_item_count or 0)} item(s)): {self.checklist_missing_items or ''}",
                    "qty":            1,
                    "rate":           missing_acc_charge,
                    "income_account": income_account,
                    "cost_center":    cost_center,
                })

            if fines_total > 0:
                fine_details = frappe.db.sql("""
                    SELECT rop_reference_number, fine_date, fine_amount, violation_type
                    FROM `tabTraffic Fine`
                    WHERE matched_contract=%s AND recovery_decision='Charge to Customer' AND docstatus=1
                    ORDER BY fine_date
                """, self.name, as_dict=True)
                fine_lines = "\n".join(
                    f"  • {f.fine_date}  ROP:{f.rop_reference_number}  {f.violation_type}  OMR {flt(f.fine_amount):,.3f}"
                    for f in fine_details
                ) if fine_details else ""
                si.append("items", {
                    "item_code":      RENTAL_ITEMS["traffic_fine_ref"]["item_code"],
                    "item_name":      RENTAL_ITEMS["traffic_fine_ref"]["item_name"],
                    "description":    f"Traffic Fines — billed via separate Recovery Invoices.\nTotal: OMR {flt(fines_total):,.3f}\n{fine_lines}\n(Reference only — amount OMR 0.000 to avoid double billing)",
                    "qty":            1,
                    "rate":           0,
                    "income_account": income_account,
                    "cost_center":    cost_center,
                })

            si.set_missing_values()
            si.run_method("calculate_taxes_and_totals")
            grand_total = flt(si.grand_total or 0)

            def _set(f, v):
                if hasattr(si, f):
                    setattr(si, f, v)

            _set("advance_applied",            total_advance)
            _set("deposit_collected",          total_deposit)
            _set("deposit_used_for_damage",    deposit_used_for_damage)
            _set("deposit_applied_to_invoice", deposit_applied_invoice)
            _set("deposit_refund_due",         deposit_refund)
            _set("net_amount_due",             net_due)
            _set("traffic_fines_total",        fines_total)

            si.remarks = self._build_remarks(
                grand_total, total_advance, total_deposit,
                deposit_used_for_damage, deposit_applied_invoice,
                deposit_refund, net_due, fines_total, missing_acc_charge,
            )

            si.insert(ignore_permissions=True)
            si.submit()
            self.db_set("sales_invoice", si.name)
            self._notify_invoice_created(si.name, net_due, deposit_refund, fines_total)

        except frappe.ValidationError:
            raise
        except Exception:
            frappe.log_error(frappe.get_traceback(), "Rental Invoice Creation Error")
            frappe.throw("Invoice creation failed. Check the Error Log for details.", title="Invoice Error")

    # ── Helpers ──────────────────────────────────────────────────────────────

    def _get_income_account(self):
        acc = frappe.db.get_value("Account", {"company": self.company, "root_type": "Income", "is_group": 0, "account_name": ["like", "%Rental%"]}, "name")
        if not acc:
            acc = frappe.db.get_value("Account", {"company": self.company, "root_type": "Income", "is_group": 0}, "name")
        return acc

    def _build_remarks(
        self, grand_total, total_advance, total_deposit,
        deposit_used_for_damage, deposit_applied_invoice,
        deposit_refund, net_due, fines_total, missing_acc_charge,
    ):
        S = "─" * 52
        lines = [
            "RENTAL CONTRACT — CLOSING INVOICE", S,
            f"Contract   : {self.name}",
            f"Vehicle    : {self.vehicle}",
            f"Customer   : {self.customer}",
            f"Period     : {self.date_out} → {self.actual_return_date or self.date_return}",
            f"Days       : {self.total_days or 0}", S,
            "CHARGES",
            f"  Base Rental              : OMR {flt(self.base_rental_amount or 0):>10,.3f}",
        ]
        if flt(self.excess_km_charges or 0) > 0:
            lines.append(f"  Excess KM                : OMR {flt(self.excess_km_charges):>10,.3f}")
        if flt(self.late_return_charge or 0) > 0:
            lines.append(f"  Late Return              : OMR {flt(self.late_return_charge):>10,.3f}")
        if flt(self.damage_charges or 0) > 0:
            lines.append(f"  Damage / Penalties       : OMR {flt(self.damage_charges):>10,.3f}")
        if missing_acc_charge > 0:
            lines.append(f"  Missing Accessories      : OMR {missing_acc_charge:>10,.3f}  ({int(self.checklist_missing_item_count or 0)} item(s))")
        lines += [
            f"  VAT                      : OMR {flt(self.vat_amount or 0):>10,.3f}",
            f"  Invoice Total            : OMR {grand_total:>10,.3f}", S,
            "VEHICLE CONDITION (Post-Return Checklist)",
        ]
        if self.return_checklist:
            lines.append(f"  ⚠️  Missing              : {self.checklist_missing_items}" if self.checklist_missing_item_count else "  ✅ All accessories present")
            panel_dmg = self.new_damage_panels or ""
            lines.append(f"  🔴 New damage            :\n{panel_dmg}" if panel_dmg and "No new damage" not in panel_dmg else "  ✅ No new panel damage")
        else:
            lines.append("  (Post-Return Checklist not linked)")
        lines += [S, "TRAFFIC FINES"]

        all_fines = frappe.db.sql("""
            SELECT rop_reference_number, fine_date, fine_amount, violation_type, recovery_decision, recovery_status
            FROM `tabTraffic Fine`
            WHERE matched_contract=%s AND docstatus=1
            ORDER BY fine_date
        """, self.name, as_dict=True) if self.name else []

        if all_fines:
            for f in all_fines:
                status_tag = {"Charge to Customer": "CHARGED", "Absorb Internally": "ABSORBED", "Under Dispute": "DISPUTED", "Pending Review": "⚠️ PENDING"}.get(f.recovery_decision, f.recovery_decision)
                lines.append(f"  {f.fine_date}  ROP:{f.rop_reference_number}  {f.violation_type}  OMR {flt(f.fine_amount):,.3f}  [{status_tag}]")
            if fines_total > 0:
                lines.append(f"  Total charged to customer: OMR {fines_total:,.3f}")
        else:
            lines.append("  ✅ No traffic fines on this contract")

        lines += [
            S, "PAYMENT & SETTLEMENT",
            f"  Advance Paid             : OMR {total_advance:>10,.3f}  (deducted)",
            f"  Security Deposit Held    : OMR {total_deposit:>10,.3f}",
        ]
        if deposit_used_for_damage > 0:
            lines.append(f"  Deposit → Damage         : OMR {deposit_used_for_damage:>10,.3f}  (applied)")
        if deposit_applied_invoice > 0:
            lines.append(f"  Deposit → Invoice        : OMR {deposit_applied_invoice:>10,.3f}  (applied)")
        lines.append(f"  ← Deposit Refund         : OMR {deposit_refund:>10,.3f}  ← DUE TO CUSTOMER" if deposit_refund > 0 else "  Deposit Refund           : OMR       0.000  (no refund due)")
        lines.append(S)
        lines.append(f"  ★ NET DUE FROM CUSTOMER  : OMR {net_due:>10,.3f}" if net_due > 0 else "  ✅ Fully settled — zero balance due")
        if deposit_refund > 0:
            lines.append(f"  ⚠️  REFUND DUE TO CUST   : OMR {deposit_refund:>10,.3f}  ← ACTION NEEDED")
        return "\n".join(lines)

    def _notify_invoice_created(self, invoice_name, net_due, deposit_refund, fines_total):
        parts = [f"✅ Sales Invoice <b>{invoice_name}</b> created and submitted."]
        if net_due > 0:
            parts.append(f"<br>💰 Net Amount Due: <b>OMR {flt(net_due):,.3f}</b>")
        else:
            parts.append("<br>✅ Fully settled — zero balance due.")
        if deposit_refund > 0:
            parts.append(f"<br><br>⚠️ <b>Deposit Refund Required: OMR {flt(deposit_refund):,.3f}</b><br>Please process the refund to the customer.")
        if fines_total > 0:
            parts.append(f"<br><br>📋 Traffic Fines (charged to customer): OMR {flt(fines_total):,.3f} — shown as reference line. Recovery Invoices were created per fine.")
        if self.checklist_missing_item_count:
            parts.append(f"<br><br>⚠️ Missing Accessories: {int(self.checklist_missing_item_count)} item(s) — OMR {flt(self.checklist_missing_accessories_charge):,.3f} charged.")
        frappe.msgprint(
            "".join(parts),
            title="Contract Closed — Invoice Created",
            indicator="orange" if deposit_refund > 0 else "green",
        )

    # ── Setters ──────────────────────────────────────────────────────────────

    def set_customer_kyc(self):
        if self.customer and not self.customer_kyc:
            self.customer_kyc = frappe.db.get_value("Customer KYC", {"customer": self.customer}, "name")

    def set_customer_details(self):
        if not self.customer_kyc:
            return
        kyc = frappe.db.get_value(
            "Customer KYC", self.customer_kyc,
            ["mobile_number", "licence_expiry_date", "customer_type",
             "residential_address", "work_address",
             "residential_address_descriptive", "workoffice_address_descriptive"],
            as_dict=True,
        )
        if not kyc:
            return
        self.customer_mobile = kyc.get("mobile_number") or ""
        self.licence_expiry  = kyc.get("licence_expiry_date") or None
        self.customer_type   = kyc.get("customer_type") or ""

        res_addr = kyc.get("residential_address_descriptive") or ""
        if not res_addr and kyc.get("residential_address"):
            res_addr = self._resolve_address(kyc["residential_address"])
        self.residential_address = res_addr

        work_addr = kyc.get("workoffice_address_descriptive") or ""
        if not work_addr and kyc.get("work_address"):
            work_addr = self._resolve_address(kyc["work_address"])
        self.work_address = work_addr

    def _resolve_address(self, address_name: str) -> str:
        if not address_name:
            return ""
        addr = frappe.db.get_value("Address", address_name, ["address_line1", "address_line2", "city", "state", "country"], as_dict=True)
        if not addr:
            return ""
        return ", ".join(p for p in [addr.get("address_line1"), addr.get("address_line2"), addr.get("city"), addr.get("state"), addr.get("country")] if p)

    def set_vehicle_status_snapshot(self):
        if self.vehicle:
            self.vehicle_status_at_contract = frappe.db.get_value("Vehicle Master", self.vehicle, "vehicle_status")

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

    # ── Validators (stubs — fill business rules as needed) ──────────────────

    def validate_customer_kyc(self): pass
    def validate_licence(self):      pass
    def validate_vehicle(self):      pass
    def validate_dates(self):        pass
    def validate_km(self):           pass
    def validate_rate_card(self):    pass

    # ── Charge calculation ───────────────────────────────────────────────────

    def calculate_charges(self):
        rate       = flt(self.rate or 0)
        total_days = self.total_days or 0

        self.base_rental_amount = rate * total_days
        self.excess_km_charges  = 0

        if self.contract_type == "Daily":
            free_km       = (self.free_km_per_day or 0) * total_days
            extra_km_rate = flt(self.excess_km_charge_daily or 0)
        elif self.contract_type == "Weekly":
            weeks         = total_days // 7
            free_km       = (self.free_km_per_week or 0) * weeks
            extra_km_rate = flt(self.excess_km_charge_daily or 0)
        elif self.contract_type == "Monthly":
            months        = total_days // 30
            free_km       = (self.free_km_per_month or 0) * months
            extra_km_rate = flt(self.excess_km_charge_monthly or 0)
        else:
            free_km       = 0
            extra_km_rate = 0

        if self.km_used is not None and self.km_used > free_km:
            self.excess_km_charges = (self.km_used - free_km) * extra_km_rate

        self._calculate_late_charge()

        self.gross_amount = flt(self.base_rental_amount) + flt(self.excess_km_charges)
        vat               = self.get_vat_rate()
        self.vat_amount   = (self.gross_amount * vat) / 100
        self.total_amount = self.gross_amount + self.vat_amount

    def _calculate_late_charge(self):
        if not self.actual_return_date or not self.date_return:
            return
        overdue_days  = date_diff(self.actual_return_date, self.date_return)
        overdue_hours = 0

        if overdue_days == 0 and self.actual_return_time and self.time_return:
            try:
                overdue_hours = time_diff_in_hours(
                    str(self.actual_return_date) + " " + str(self.actual_return_time),
                    str(self.date_return) + " " + str(self.time_return),
                )
            except Exception:
                overdue_hours = 0

        grace = flt(self.grace_period_hours or 0)
        mode  = self.late_return_billing_mode or "Full Extra Day"

        if overdue_days <= 0 and overdue_hours <= grace:
            self.late_return_days   = 0
            self.late_return_charge = 0
            return

        self.late_return_days = overdue_days or round(overdue_hours / 24, 2)

        if mode == "No Charge":
            self.late_return_charge = 0
        elif mode == "Hourly Rate":
            total_late_hours        = (overdue_days * 24) + max(overdue_hours - grace, 0)
            self.late_return_charge = total_late_hours * flt(self.hourly_late_rate or 0)
        elif mode == "Half Day":
            half_days               = overdue_days * 2 + (1 if overdue_hours > grace else 0)
            self.late_return_charge = half_days * flt(self.rate or 0) / 2
        else:
            self.late_return_charge = overdue_days * flt(self.rate or 0)

    def update_contract_status(self):
        if self.contract_status == "Cancelled":
            return
        if self.actual_return_date and self.km_return is not None:
            self.contract_status = "Closed"
        elif self.actual_return_date:
            self.contract_status = "Pending Return"
        else:
            self.contract_status = "Active"

    def get_vat_rate(self):
        vat = frappe.db.get_value("VAT Configuration", {"company": self.company, "is_active": 1}, "vat_rate")
        if vat:
            return flt(vat)
        vat_label = self.vat_rate or ""
        if vat_label.startswith("5"):
            return 5.0
        elif vat_label.startswith("15"):
            return 15.0
        return 0.0


# ── Whitelisted API for PDC lookup from contract form ────────────────────────

@frappe.whitelist()
def get_pdcs_for_contract(rental_contract: str):
    """Fetch all PDC Register records linked to this rental contract — used in form dashboard."""
    return frappe.db.get_all(
        "PDC Register",
        filters={"rental_contract": rental_contract},
        fields=[
            "name", "cheque_number", "issuing_bank", "cheque_amount",
            "cheque_post_date", "pdc_status", "realisation_date",
            "bounce_reason", "representation_count",
        ],
        order_by="cheque_post_date asc",
    )