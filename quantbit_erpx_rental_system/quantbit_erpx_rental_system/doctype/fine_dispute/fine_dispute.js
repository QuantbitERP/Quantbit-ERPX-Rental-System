
// Copyright (c) 2026, Quantbit Technologies Pvt. Ltd.
// fine_dispute.js

frappe.ui.form.on('Fine Dispute', {

    refresh: function(frm) {
        frm.trigger("set_status_indicator");
        frm.trigger("add_action_buttons");
    },

    resolution_decision: function(frm) {
        // Auto-fill resolution date when a decision is made
        if (frm.doc.resolution_decision && !frm.doc.resolution_date) {
            frm.set_value("resolution_date", frappe.datetime.get_today());
        }
        if (!frm.doc.resolved_by) {
            frm.set_value("resolved_by", frappe.session.user);
        }
    },

    // ─────────────────────────────────────────────
    //  STATUS INDICATOR
    // ─────────────────────────────────────────────

    set_status_indicator: function(frm) {
        if (frm.is_new()) return;

        const status = frm.doc.dispute_status;
        const colour_map = {
            "Under Investigation"               : "orange",
            "Resolved — Absorbed"               : "grey",
            "Resolved — Charged to Customer"    : "green",
            "Escalated to ROP"                  : "red",
            "Withdrawn"                          : "grey",
        };

        const msgs = {
            "Under Investigation"           :
                "🔍 This dispute is under investigation. Fill in details and set a resolution decision.",
            "Resolved — Absorbed"           :
                "✅ Resolved: Fine absorbed internally. GL entry was posted.",
            "Resolved — Charged to Customer":
                "✅ Resolved: Charged to customer. Recovery Invoice created.",
            "Escalated to ROP"              :
                "⚠️ Escalated to ROP. Follow up externally and update once resolved.",
            "Withdrawn"                     :
                "Dispute withdrawn / cancelled.",
        };

        frm.set_intro(
            msgs[status] || "",
            colour_map[status] || "blue"
        );
    },

    // ─────────────────────────────────────────────
    //  ACTION BUTTONS
    // ─────────────────────────────────────────────

    add_action_buttons: function(frm) {
        if (frm.is_new()) return;

        // ── View the source Traffic Fine ──
        if (frm.doc.traffic_fine) {
            frm.add_custom_button(__("View Traffic Fine"), function() {
                frappe.set_route("Form", "Traffic Fine", frm.doc.traffic_fine);
            }, __("Links"));
        }

        // ── View matched rental contract via the fine ──
        if (frm.doc.traffic_fine) {
            frappe.db.get_value(
                "Traffic Fine",
                frm.doc.traffic_fine,
                "matched_contract",
                function(r) {
                    if (r && r.matched_contract) {
                        frm.add_custom_button(__("View Rental Contract"), function() {
                            frappe.set_route("Form", "Rental Contract", r.matched_contract);
                        }, __("Links"));
                    }
                }
            );
        }
    },
});