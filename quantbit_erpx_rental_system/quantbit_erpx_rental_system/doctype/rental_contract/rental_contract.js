// Copyright (c) 2026, Quantbit Technologies Pvt. Ltd. and contributors
// For license information, please see license.txt

frappe.ui.form.on('Rental Contract', {

    // ─────────────────────────────────────────────
    //  FORM EVENTS
    // ─────────────────────────────────────────────

    refresh: function(frm) {
        frm.trigger("add_handover_buttons");
    },

    vehicle: function(frm) {
        if (frm.doc.vehicle) {
            frappe.db.get_value('Vehicle Master', frm.doc.vehicle, 'vehicle_status')
                .then(r => {
                    if (r.message) {
                        frm.set_value('vehicle_status_at_contract', r.message.vehicle_status);
                        if (r.message.vehicle_status !== "Available") {
                            frappe.msgprint("⚠ Vehicle is not available");
                        }
                    }
                });
        }
    },

    rate_card: function(frm) {
        frm.trigger("map_rate_card");
    },

    contract_type: function(frm) {
        if (frm.doc.rate_card) {
            frm.trigger("map_rate_card");
        }
    },

    km_return: function(frm) {
        frm.trigger("recalculate");
    },

    actual_return_date: function(frm) {
        frm.trigger("recalculate");
    },

    // ─────────────────────────────────────────────
    //  BUTTONS
    // ─────────────────────────────────────────────

    add_handover_buttons: function(frm) {

        // Only show buttons on saved (not new) records
        if (frm.is_new()) return;

        // ── Pre-Delivery Handover Checklist button ──
        // Show if: contract not yet submitted OR submitted but checklist not linked yet
        if (frm.doc.docstatus === 0 || !frm.doc.handover_checklist) {
            frm.add_custom_button(
                __("Pre-Delivery Checklist"),
                function() {
                    // If already linked, open the existing one
                    if (frm.doc.handover_checklist) {
                        frappe.set_route(
                            "Form",
                            "Handover Checklist",
                            frm.doc.handover_checklist
                        );
                        return;
                    }

                    // Otherwise create a new one pre-filled
                    frappe.new_doc("Handover Checklist", {
                        rental_contract : frm.doc.name,
                        checklist_type  : "Pre-Delivery (Handover)",
                        customer        : frm.doc.customer,
                        vehicle         : frm.doc.vehicle,
                        date_out        : frm.doc.date_out,
                        km_out          : frm.doc.km_out,
                        fuel_level_out  : frm.doc.fuel_level_out,
                    });
                },
                __("Checklists")   // button group label
            );
        }

        // If already linked, show a quick-open button instead
        if (frm.doc.handover_checklist) {
            frm.add_custom_button(
                __("View Pre-Delivery Checklist"),
                function() {
                    frappe.set_route(
                        "Form",
                        "Handover Checklist",
                        frm.doc.handover_checklist
                    );
                },
                __("Checklists")
            );
        }

        // ── Post-Return Checklist button ──
        // Only relevant after submission + actual_return_date is filled
        if (frm.doc.docstatus === 1 && frm.doc.actual_return_date) {

            if (frm.doc.return_checklist) {
                // Already exists — just open it
                frm.add_custom_button(
                    __("View Post-Return Checklist"),
                    function() {
                        frappe.set_route(
                            "Form",
                            "Handover Checklist",
                            frm.doc.return_checklist
                        );
                    },
                    __("Checklists")
                );
            } else {
                // Create new post-return checklist pre-filled
                frm.add_custom_button(
                    __("Post-Return Checklist"),
                    function() {
                        frappe.new_doc("Handover Checklist", {
                            rental_contract    : frm.doc.name,
                            checklist_type     : "Post-Return",
                            customer           : frm.doc.customer,
                            vehicle            : frm.doc.vehicle,
                            actual_return_date : frm.doc.actual_return_date,
                            km_return          : frm.doc.km_return,
                            fuel_level_return  : frm.doc.fuel_level_return,
                        });
                    },
                    __("Checklists")
                );
            }
        }

        // ── Highlight the submit button if checklist is missing ──
        // Gives staff a visual cue before they even try to submit
        if (frm.doc.docstatus === 0 && !frm.doc.handover_checklist) {
            frm.set_intro(
                "⚠️ Pre-Delivery Handover Checklist not completed yet. " +
                "Use the <b>Checklists → Pre-Delivery Checklist</b> button above to create it.",
                "orange"
            );
        } else if (frm.doc.docstatus === 0 && frm.doc.handover_checklist) {
            frm.set_intro(
                "✅ Handover Checklist is linked. You can now submit this contract.",
                "green"
            );
        }
    },

    // ─────────────────────────────────────────────
    //  CUSTOM TRIGGERS
    // ─────────────────────────────────────────────

    map_rate_card: function(frm) {
        if (!frm.doc.rate_card || !frm.doc.contract_type) return;

        frappe.call({
            method: "frappe.client.get",
            args: {
                doctype: "Rate Card",
                name: frm.doc.rate_card
            },
            callback: function(r) {
                if (!r.message) return;

                let rc = r.message;

                if (frm.doc.contract_type === "Daily") {
                    frm.set_value("rate",                   rc.daily_rate              || 0);
                    frm.set_value("free_km_per_day",        rc.free_km_per_day         || 0);
                    frm.set_value("excess_km_charge_daily", rc.excess_km_charge_daily  || 0);
                }
                else if (frm.doc.contract_type === "Weekly") {
                    frm.set_value("rate",                   rc.weekly_rate             || 0);
                    frm.set_value("free_km_per_week",       rc.free_km_per_week        || 0);
                    frm.set_value("excess_km_charge_daily", rc.excess_km_charge_daily  || 0);
                }
                else if (frm.doc.contract_type === "Monthly") {
                    frm.set_value("rate",                    rc.monthly_rate             || 0);
                    frm.set_value("free_km_per_month",       rc.free_km_per_month        || 0);
                    frm.set_value("excess_km_charge_monthly",rc.excess_km_charge_monthly || 0);
                }
            }
        });
    }

});