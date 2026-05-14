// Copyright (c) 2026, Quantbit Technologies Pvt. Ltd. and contributors
// For license information, please see license.txt

frappe.ui.form.on('Rental Contract', {

    // ─────────────────────────────────────────────
    //  FORM SETUP — runs once on form load
    // ─────────────────────────────────────────────

    setup: function(frm) {
        // FIX A: Filter vehicle link field to show only Available vehicles
        frm.set_query("vehicle", function() {
            return { filters: { vehicle_status: "Available" } };
        });
    },

    // ─────────────────────────────────────────────
    //  REFRESH — runs on every form open/reload
    // ─────────────────────────────────────────────

    refresh: function(frm) {
        frm.trigger("add_handover_buttons");
        frm.trigger("add_traffic_fine_buttons");
        frm.trigger("add_deposit_billing_summary"); // FIX C: live billing card
    },

    // ─────────────────────────────────────────────
    //  FIELD CHANGE EVENTS
    // ─────────────────────────────────────────────

    vehicle: function(frm) {
        // Warn if staff somehow selects a non-available vehicle (backup check)
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
        // Re-map rate card when contract type changes
        if (frm.doc.rate_card) frm.trigger("map_rate_card");
    },

    km_return: function(frm) {
        frm.trigger("recalculate");
    },

    // FIX B: Re-render buttons + billing summary when return date is filled
    actual_return_date: function(frm) {
        frm.trigger("recalculate");
        frm.trigger("add_handover_buttons");
        frm.trigger("add_deposit_billing_summary");
    },

    // Refresh billing summary when financial fields change
    advance_amount:          function(frm) { frm.trigger("add_deposit_billing_summary"); },
    security_deposit:        function(frm) { frm.trigger("add_deposit_billing_summary"); },
    deposit_settlement_mode: function(frm) { frm.trigger("add_deposit_billing_summary"); },
    damage_charges:          function(frm) { frm.trigger("add_deposit_billing_summary"); },

    recalculate: function(frm) {
        // Placeholder — server-side calculate_charges handles the actual math
        frm.trigger("add_deposit_billing_summary");
    },

    // ─────────────────────────────────────────────
    //  FIX C — DEPOSIT & BILLING SUMMARY CARD
    //  Renders a live HTML summary below the billing fields showing:
    //  advance paid, deposit held, settlement mode, net due, refund status.
    // ─────────────────────────────────────────────

    add_deposit_billing_summary: function(frm) {
        // Remove any existing summary card before re-rendering
        frm.fields_dict["pdc_required"] &&
            $(frm.fields_dict["pdc_required"].wrapper).find(".billing-summary-card").remove();

        let advance  = flt(frm.doc.advance_amount   || 0);
        let deposit  = flt(frm.doc.security_deposit || 0);
        let total    = flt(frm.doc.total_amount      || 0);
        let damage   = flt(frm.doc.damage_charges    || 0);
        let mode     = frm.doc.deposit_settlement_mode || "—";
        let advMethod = frm.doc.advance_payment_method || "—";
        let depMethod = frm.doc.deposit_method         || "—";

        // Mirror Python settlement logic for live preview
        let depositUsedForDamage       = Math.min(damage, deposit);
        let depositRefund              = deposit - depositUsedForDamage;
        let depositAppliedToInvoice    = 0;

        if (mode === "Apply to Final Invoice First") {
            depositAppliedToInvoice = Math.max(Math.min(depositRefund, total - advance), 0);
            depositRefund -= depositAppliedToInvoice;
        }

        let netDue = Math.max(total - advance - depositAppliedToInvoice, 0);

        // Helper: format as OMR 0.000
        let fmt = v => `OMR ${flt(v).toFixed(3)}`;

        // Build coloured rows: green = good, orange = action needed, grey = info
        let rows = `
            <tr><td>💵 Advance Paid</td>
                <td style="text-align:right"><b>${fmt(advance)}</b></td>
                <td style="color:#555">${advMethod}</td></tr>
            <tr><td>🔒 Security Deposit</td>
                <td style="text-align:right"><b>${fmt(deposit)}</b></td>
                <td style="color:#555">${depMethod}</td></tr>
            <tr><td>📋 Settlement Mode</td>
                <td colspan="2" style="color:#555">${mode}</td></tr>
        `;

        if (depositUsedForDamage > 0) {
            rows += `<tr style="color:#c62828"><td>🔴 Deposit → Damage</td>
                <td style="text-align:right"><b>${fmt(depositUsedForDamage)}</b></td>
                <td>Applied to damage</td></tr>`;
        }
        if (depositAppliedToInvoice > 0) {
            rows += `<tr style="color:#1565c0"><td>📄 Deposit → Invoice</td>
                <td style="text-align:right"><b>${fmt(depositAppliedToInvoice)}</b></td>
                <td>Applied to invoice</td></tr>`;
        }

        // Net due row — red if amount owed, green if settled
        let netColor   = netDue > 0 ? "#b71c1c" : "#2e7d32";
        let netLabel   = netDue > 0 ? "★ Net Due from Customer" : "✅ Fully Settled";
        rows += `<tr style="color:${netColor};font-weight:bold">
                    <td>${netLabel}</td>
                    <td style="text-align:right">${fmt(netDue)}</td>
                    <td></td></tr>`;

        // Refund row — orange if refund is owed to customer
        if (depositRefund > 0) {
            rows += `<tr style="color:#e65100;font-weight:bold">
                        <td>⚠️ Refund Due to Customer</td>
                        <td style="text-align:right">${fmt(depositRefund)}</td>
                        <td>← Action Required</td></tr>`;
        }

        let html = `
            <div class="billing-summary-card" style="
                margin-top:12px;padding:12px 16px;
                border:1px solid #ddd;border-radius:6px;
                background:#fafafa;font-size:13px;">
                <div style="font-weight:600;margin-bottom:8px;color:#333">
                    💳 Deposit & Billing Summary
                </div>
                <table style="width:100%;border-collapse:collapse">
                    <colgroup>
                        <col style="width:45%"><col style="width:25%"><col style="width:30%">
                    </colgroup>
                    ${rows}
                </table>
            </div>`;

        // Inject after the pdc_required field if it exists
        if (frm.fields_dict["pdc_required"]) {
            $(frm.fields_dict["pdc_required"].wrapper).append(html);
        }
    },

    // ─────────────────────────────────────────────
    //  HANDOVER / CHECKLIST BUTTONS
    // ─────────────────────────────────────────────

    add_handover_buttons: function(frm) {
        if (frm.is_new()) return;

        // Pre-Delivery: show Create button if not yet linked
        if (frm.doc.docstatus === 0 || !frm.doc.handover_checklist) {
            frm.add_custom_button(__("Pre-Delivery Checklist"), function() {
                if (frm.doc.handover_checklist) {
                    frappe.set_route("Form", "Handover Checklist", frm.doc.handover_checklist);
                    return;
                }
                frappe.new_doc("Handover Checklist", {
                    rental_contract: frm.doc.name,
                    checklist_type:  "Pre-Delivery (Handover)",
                    customer:        frm.doc.customer,
                    vehicle:         frm.doc.vehicle,
                    date_out:        frm.doc.date_out,
                    km_out:          frm.doc.km_out,
                    fuel_level_out:  frm.doc.fuel_level_out,
                });
            }, __("Checklists"));
        }

        // Pre-Delivery: show View button if already linked
        if (frm.doc.handover_checklist) {
            frm.add_custom_button(__("View Pre-Delivery Checklist"), function() {
                frappe.set_route("Form", "Handover Checklist", frm.doc.handover_checklist);
            }, __("Checklists"));
        }

        // Post-Return: show as soon as actual_return_date is filled on a submitted doc
        // FIX B: mirrors Pre-Delivery UX — button not a popup
        if (frm.doc.docstatus === 1 && frm.doc.actual_return_date) {
            if (frm.doc.return_checklist) {
                frm.add_custom_button(__("View Post-Return Checklist"), function() {
                    frappe.set_route("Form", "Handover Checklist", frm.doc.return_checklist);
                }, __("Checklists"));
            } else {
                frm.add_custom_button(__("Post-Return Checklist"), function() {
                    frappe.new_doc("Handover Checklist", {
                        rental_contract:  frm.doc.name,
                        checklist_type:   "Post-Return",
                        checklist_date:   frappe.datetime.get_today(),
                        customer:         frm.doc.customer,
                        vehicle:          frm.doc.vehicle,
                        odometer_reading: frm.doc.km_return || 0,
                        fuel_level:       frm.doc.fuel_level_return || "",
                    });
                }, __("Checklists"));
            }
        }

        // Intro banners — guide staff to the right next step
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
        } else if (frm.doc.docstatus === 1 && frm.doc.actual_return_date && !frm.doc.return_checklist) {
            frm.set_intro(
                "⚠️ Vehicle return date is set. " +
                "Use the <b>Checklists → Post-Return Checklist</b> button above to complete the return.",
                "orange"
            );
        } else if (frm.doc.docstatus === 1 && frm.doc.return_checklist) {
            frm.set_intro(
                "✅ Post-Return Checklist is linked. Contract will close once checklist is submitted.",
                "green"
            );
        }
    },

    // ─────────────────────────────────────────────
    //  TRAFFIC FINE BUTTONS
    //  Only shown on submitted contracts (docstatus === 1)
    // ─────────────────────────────────────────────

    add_traffic_fine_buttons: function(frm) {
        if (frm.is_new() || frm.doc.docstatus !== 1) return;

        // View all fines linked to this contract
        frm.add_custom_button(__("View Traffic Fines"), function() {
            frappe.set_route("List", "Traffic Fine", { matched_contract: frm.doc.name });
        }, __("Linked Documents"));

        // Raise a dispute against a specific fine on this contract
        frm.add_custom_button(__("Raise Fine Dispute"), function() {
            let d = new frappe.ui.Dialog({
                title: __("Raise Fine Dispute"),
                fields: [{
                    fieldname: "traffic_fine",
                    fieldtype: "Link",
                    label: __("Traffic Fine"),
                    options: "Traffic Fine",
                    reqd: 1,
                    get_query: function() {
                        return { filters: { matched_contract: frm.doc.name } };
                    }
                }],
                primary_action_label: __("Create Dispute"),
                primary_action(values) {
                    frappe.new_doc("Fine Dispute", {
                        traffic_fine: values.traffic_fine,
                        customer:     frm.doc.customer,
                        dispute_date: frappe.datetime.get_today(),
                    });
                    d.hide();
                }
            });
            d.show();
        }, __("Linked Documents"));

        // Banner warning if fines are charged to customer
        if ((frm.doc.traffic_fines_total || 0) > 0) {
            frm.set_intro(
                `⚠️ This contract has <b>OMR ${flt(frm.doc.traffic_fines_total).toFixed(3)}</b> ` +
                `in traffic fines charged to the customer. ` +
                `<a href="#" onclick="frappe.set_route('List','Traffic Fine',` +
                `{'matched_contract':'${frm.doc.name}'});return false;">View Fines →</a>`,
                "orange"
            );
        }
    },

    // ─────────────────────────────────────────────
    //  MAP RATE CARD  — fills rate + KM fields from selected rate card
    // ─────────────────────────────────────────────

    map_rate_card: function(frm) {
        if (!frm.doc.rate_card || !frm.doc.contract_type) return;

        frappe.call({
            method: "frappe.client.get",
            args: { doctype: "Rate Card", name: frm.doc.rate_card },
            callback: function(r) {
                if (!r.message) return;
                let rc = r.message;

                if (frm.doc.contract_type === "Daily") {
                    frm.set_value("rate",                   rc.daily_rate             || 0);
                    frm.set_value("free_km_per_day",        rc.free_km_per_day        || 0);
                    frm.set_value("excess_km_charge_daily", rc.excess_km_charge_daily || 0);
                } else if (frm.doc.contract_type === "Weekly") {
                    frm.set_value("rate",                   rc.weekly_rate            || 0);
                    frm.set_value("free_km_per_week",       rc.free_km_per_week       || 0);
                    frm.set_value("excess_km_charge_daily", rc.excess_km_charge_daily || 0);
                } else if (frm.doc.contract_type === "Monthly") {
                    frm.set_value("rate",                    rc.monthly_rate             || 0);
                    frm.set_value("free_km_per_month",       rc.free_km_per_month        || 0);
                    frm.set_value("excess_km_charge_monthly",rc.excess_km_charge_monthly || 0);
                }
            }
        });
    }

});