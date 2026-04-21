# Copyright (c) 2026, Quantbit Technologies Pvt. Ltd.
# fine_dispute.py

import frappe
from frappe.model.document import Document
from frappe.utils import today, flt


class FineDispute(Document):

    # ─────────────────────────────────────────
    #  VALIDATE
    # ─────────────────────────────────────────
    def validate(self):
        self.validate_traffic_fine()

    def validate_traffic_fine(self):
        if not self.traffic_fine:
            return
        tf_status = frappe.db.get_value(
            "Traffic Fine", self.traffic_fine, "docstatus"
        )
        if tf_status == 2:
            frappe.throw(
                "⛔ The linked Traffic Fine has been cancelled. "
                "Please link an active fine.",
                title="Cancelled Fine"
            )

    # ─────────────────────────────────────────
    #  BEFORE SUBMIT
    # ─────────────────────────────────────────
    def before_submit(self):
        if not self.resolution_decision:
            frappe.throw(
                "⛔ Please set a <b>Resolution Decision</b> before submitting the dispute.",
                title="Resolution Required"
            )
        if not self.resolution_date:
            self.resolution_date = today()
        if not self.resolved_by:
            self.resolved_by = frappe.session.user

    # ─────────────────────────────────────────
    #  ON SUBMIT  — apply the resolution
    # ─────────────────────────────────────────
    def on_submit(self):
        decision = self.resolution_decision

        if decision == "Absorb Internally":
            self._resolve_absorb()

        elif decision == "Charge to Customer":
            self._resolve_charge()

        elif decision == "Escalate to ROP":
            self._resolve_escalate()

        # Update dispute_status on save
        status_map = {
            "Absorb Internally"  : "Resolved — Absorbed",
            "Charge to Customer" : "Resolved — Charged to Customer",
            "Escalate to ROP"    : "Escalated to ROP",
        }
        new_status = status_map.get(decision, "Under Investigation")
        self.db_set("dispute_status", new_status)

        # Reflect on the Traffic Fine as well
        frappe.db.set_value(
            "Traffic Fine",
            self.traffic_fine,
            "recovery_decision",
            self._map_to_fine_decision(decision)
        )

    # ─────────────────────────────────────────
    #  ON CANCEL
    # ─────────────────────────────────────────
    def on_cancel(self):
        self.db_set("dispute_status", "Withdrawn")
        # Revert fine decision to "Under Dispute"
        frappe.db.set_value(
            "Traffic Fine",
            self.traffic_fine,
            "recovery_decision",
            "Under Dispute"
        )

    # ─────────────────────────────────────────
    #  RESOLUTION HANDLERS
    # ─────────────────────────────────────────
    def _resolve_absorb(self):
        """
        Trigger the internal GL posting on the Traffic Fine
        (re-uses the same _post_internal_gl logic).
        """
        tf = frappe.get_doc("Traffic Fine", self.traffic_fine)

        # Update the fine's decision so _post_internal_gl works correctly
        tf.db_set("recovery_decision", "Absorb Internally")

        # Call the GL method directly
        tf._post_internal_gl()
        tf.db_set("recovery_status", "Written Off")

        frappe.msgprint(
            f"✅ Fine {self.traffic_fine} absorbed internally. GL entry posted.",
            indicator="blue"
        )

    def _resolve_charge(self):
        """
        Submit the Traffic Fine's recovery invoice (create if missing).
        The fine must have a matched contract and customer.
        """
        tf = frappe.get_doc("Traffic Fine", self.traffic_fine)

        if not tf.matched_contract or not tf.customer_at_fine_date:
            frappe.throw(
                "⛔ Cannot charge to customer — the Traffic Fine has no matched "
                "contract or customer. Please update the fine first.",
                title="Contract / Customer Missing"
            )

        tf.db_set("recovery_decision", "Charge to Customer")

        if tf.recovery_invoice:
            # Invoice already exists — just notify
            frappe.msgprint(
                f"Recovery Invoice <b>{tf.recovery_invoice}</b> already exists "
                f"for fine {self.traffic_fine}.",
                indicator="green"
            )
        else:
            # Create the invoice now
            tf._create_recovery_invoice()
            tf._update_contract_fine_total()

        frappe.msgprint(
            f"✅ Fine {self.traffic_fine} resolved — charged to customer.",
            indicator="green"
        )

    def _resolve_escalate(self):
        """
        Mark for ROP escalation — no GL or invoice.
        Staff must follow up with ROP externally.
        """
        frappe.db.set_value(
            "Traffic Fine",
            self.traffic_fine,
            "recovery_status",
            "Pending"
        )
        frappe.msgprint(
            f"⚠️ Fine {self.traffic_fine} escalated to ROP. "
            "Please follow up externally and update the fine once resolved.",
            title="Escalated to ROP",
            indicator="orange"
        )

    def _map_to_fine_decision(self, resolution_decision):
        return {
            "Absorb Internally"  : "Absorb Internally",
            "Charge to Customer" : "Charge to Customer",
            "Escalate to ROP"    : "Under Dispute",
        }.get(resolution_decision, "Under Dispute")