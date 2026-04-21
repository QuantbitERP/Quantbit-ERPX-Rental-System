# Copyright (c) 2026, Quantbit Technologies Pvt. Ltd. and contributors
import frappe
from frappe.model.document import Document


class HandoverChecklist(Document):

    def validate(self):
        # Block duplicate submitted checklist of same type for same contract
        if frappe.db.exists("Handover Checklist", {
            "rental_contract": self.rental_contract,
            "checklist_type": self.checklist_type,
            "docstatus": 1,
            "name": ["!=", self.name]
        }):
            frappe.throw(
                f"❌ A submitted {self.checklist_type} checklist already exists "
                f"for contract {self.rental_contract}. Cannot create another.",
                title="Duplicate Checklist"
            )

    def on_submit(self):
        if not self.rental_contract:
            frappe.throw(
                "❌ Rental Contract is not linked. "
                "Please link the contract before submitting.",
                title="Missing Contract"
            )

        contract_status = frappe.db.get_value(
            "Rental Contract", self.rental_contract, "docstatus"
        )

        if self.checklist_type == "Pre-Delivery (Handover)":
            self._handle_pre_delivery(contract_status)

        elif self.checklist_type == "Post-Return":
            self._handle_post_return(contract_status)

    # ── Pre-Delivery: write handover_checklist back to contract ──
    def _handle_pre_delivery(self, contract_status):
        # Contract can be Draft (0) or Submitted (1) at this point
        # We just write the link back — the submit gate on contract checks this
        frappe.db.set_value(
            "Rental Contract",
            self.rental_contract,
            "handover_checklist",
            self.name
        )
        frappe.msgprint(
            f"✅ Pre-Delivery Checklist submitted and linked to "
            f"<b>{self.rental_contract}</b> automatically. "
            f"You can now submit the Rental Contract.",
            title="Checklist Linked",
            indicator="green"
        )

    # ── Post-Return: write return_checklist back + validate return fields ──
    def _handle_post_return(self, contract_status):
        actual_return_date = frappe.db.get_value(
            "Rental Contract", self.rental_contract, "actual_return_date"
        )
        if not actual_return_date:
            frappe.throw(
                "❌ Please fill <b>Actual Return Date</b> on the Rental Contract "
                "before submitting the Post-Return Checklist.",
                title="Missing Return Date"
            )

        frappe.db.set_value(
            "Rental Contract",
            self.rental_contract,
            "return_checklist",
            self.name
        )
        frappe.msgprint(
            f"✅ Post-Return Checklist submitted and linked to "
            f"<b>{self.rental_contract}</b> automatically.",
            title="Return Checklist Linked",
            indicator="green"
        )