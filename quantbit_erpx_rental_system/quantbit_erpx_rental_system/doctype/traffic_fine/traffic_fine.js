// Copyright (c) 2026, Quantbit Technologies Pvt. Ltd.
// traffic_fine.js

frappe.ui.form.on('Traffic Fine', {

    // ─────────────────────────────────────────────
    //  FORM EVENTS
    // ─────────────────────────────────────────────

    refresh: function(frm) {
        frm.trigger("set_status_indicator");
        frm.trigger("add_action_buttons");
    },

    matched_contract: function(frm) {
        // When staff manually sets a contract, force match method
        if (frm.doc.matched_contract && frm.doc.match_method !== "Auto-Matched") {
            frm.set_value("match_method", "Manually Assigned");
        }
    },

    recovery_decision: function(frm) {
        frm.trigger("set_status_indicator");
    },

    // ─────────────────────────────────────────────
    //  STATUS COLOUR INDICATOR IN FORM INTRO
    // ─────────────────────────────────────────────

    set_status_indicator: function(frm) {
        if (frm.is_new()) return;

        const decision  = frm.doc.recovery_decision;
        const matched   = frm.doc.matched_contract;
        const status    = frm.doc.recovery_status;

        if (frm.doc.docstatus === 1) {
            // Submitted — show recovery status
            const colours = {
                "Invoiced"    : "blue",
                "Collected"   : "green",
                "Written Off" : "grey",
                "Pending"     : "orange",
            };
            frm.set_intro(
                `📋 Fine submitted — Recovery Status: <b>${status}</b>`,
                colours[status] || "orange"
            );
            return;
        }

        if (!matched && decision !== "Absorb Internally") {
            frm.set_intro(
                "⚠️ No matching Rental Contract found for this vehicle on the fine date. " +
                "You can assign one manually or set decision to <b>Absorb Internally</b>.",
                "orange"
            );
        } else if (decision === "Pending Review") {
            frm.set_intro(
                "🔍 Review this fine and set a <b>Recovery Decision</b> before submitting.",
                "yellow"
            );
        } else if (decision === "Charge to Customer") {
            frm.set_intro(
                `✅ Will be charged to customer <b>${frm.doc.customer_at_fine_date || ""}</b> ` +
                `via contract <b>${matched}</b>. A Recovery Invoice will be created on submit.`,
                "green"
            );
        } else if (decision === "Absorb Internally") {
            frm.set_intro(
                "🏢 Fine will be absorbed internally. A GL entry will be posted on submit.",
                "blue"
            );
        } else if (decision === "Under Dispute") {
            frm.set_intro(
                "⚖️ Fine is under dispute. A Fine Dispute record will be created on submit.",
                "orange"
            );
        }
    },

    // ─────────────────────────────────────────────
    //  ACTION BUTTONS
    // ─────────────────────────────────────────────

    add_action_buttons: function(frm) {
        if (frm.is_new()) return;

        // ── View matched contract ──
        if (frm.doc.matched_contract) {
            frm.add_custom_button(__("View Contract"), function() {
                frappe.set_route("Form", "Rental Contract", frm.doc.matched_contract);
            }, __("Links"));
        }

        // ── View recovery invoice ──
        if (frm.doc.recovery_invoice) {
            frm.add_custom_button(__("View Recovery Invoice"), function() {
                frappe.set_route("Form", "Sales Invoice", frm.doc.recovery_invoice);
            }, __("Links"));
        }

        // ── View dispute ──
        if (frm.doc.docstatus === 1 && frm.doc.recovery_decision === "Under Dispute") {
            frappe.db.get_value(
                "Fine Dispute",
                {"traffic_fine": frm.doc.name},
                "name",
                function(r) {
                    if (r && r.name) {
                        frm.add_custom_button(__("View Dispute"), function() {
                            frappe.set_route("Form", "Fine Dispute", r.name);
                        }, __("Links"));
                    }
                }
            );
        }

        // ── View all fines in same batch ──
        if (frm.doc.import_batch_id) {
            frm.add_custom_button(__("View Import Batch"), function() {
                frappe.set_route("List", "Traffic Fine", {
                    "import_batch_id": frm.doc.import_batch_id
                });
            }, __("Import"));
        }

        // ── Mark WhatsApp sent ──
        if (
            frm.doc.docstatus === 1 &&
            frm.doc.recovery_decision === "Charge to Customer" &&
            !frm.doc.whatsapp_alert_sent
        ) {
            frm.add_custom_button(__("Mark WhatsApp Sent"), function() {
                frappe.confirm(
                    "Confirm that the WhatsApp alert was sent to the customer?",
                    function() {
                        frm.set_value("whatsapp_alert_sent", 1);
                        frm.set_value("whatsapp_sent_date", frappe.datetime.get_today());
                        frm.save();
                    }
                );
            }, __("Actions"));
        }
    },
});


// ─────────────────────────────────────────────────────────────────────────────
//  ROP CSV IMPORT — List View button
//  Add this to traffic_fine_list.js  OR call via a Custom Page / Workspace
// ─────────────────────────────────────────────────────────────────────────────
frappe.listview_settings["Traffic Fine"] = {
    add_fields: ["recovery_decision", "recovery_status", "match_method", "import_batch_id"],

    get_indicator: function(doc) {
        const map = {
            "Pending Review"    : ["Pending Review",   "yellow"],
            "Charge to Customer": ["Charge Customer",  "blue"  ],
            "Absorb Internally" : ["Absorb Internally","grey"  ],
            "Under Dispute"     : ["Under Dispute",    "orange"],
        };
        const recovery_status_map = {
            "Invoiced"   : ["Invoiced",    "blue"  ],
            "Collected"  : ["Collected",   "green" ],
            "Written Off": ["Written Off", "grey"  ],
        };
        if (doc.docstatus === 1 && recovery_status_map[doc.recovery_status]) {
            return recovery_status_map[doc.recovery_status];
        }
        return map[doc.recovery_decision] || ["Draft", "grey"];
    },

    onload: function(listview) {
        // ── Import ROP CSV button in list toolbar ──
        listview.page.add_inner_button(__("Import ROP CSV"), function() {
            _show_rop_import_dialog();
        });
    },
};


function _show_rop_import_dialog() {
    const d = new frappe.ui.Dialog({
        title: __("Import ROP Traffic Fines (CSV)"),
        fields: [
            {
                fieldtype: "HTML",
                fieldname: "instructions",
                options: `
                    <div class="alert alert-info" style="font-size:13px;">
                        <b>Required CSV columns (header row must be present):</b><br>
                        <code>rop_reference_number, vehicle, fine_date, fine_time,
                        violation_type, fine_amount, fine_location, rop_officer_id</code><br><br>
                        • <b>fine_date</b> format: <code>YYYY-MM-DD</code><br>
                        • <b>violation_type</b> must match:
                        Speeding / Red Light / Parking Violation / Mobile Phone Use /
                        Seatbelt / Wrong Lane / Overloading / Driving Without Licence / Other<br>
                        • Duplicate <code>rop_reference_number</code> rows are skipped automatically.
                    </div>
                `
            },
            {
                fieldtype: "Attach",
                fieldname: "csv_file",
                label: "CSV File",
                reqd: 1,
                options: { restrictions: { allowed_file_types: [".csv"] } }
            }
        ],
        primary_action_label: __("Import"),
        primary_action: function(values) {
            if (!values.csv_file) {
                frappe.msgprint("Please attach a CSV file.");
                return;
            }

            d.hide();
            frappe.show_progress(__("Importing ROP Fines"), 0, 100, __("Reading file…"));

            // Fetch the file content then call the import API
            fetch(values.csv_file)
                .then(r => r.text())
                .then(content => {
                    frappe.show_progress(__("Importing ROP Fines"), 40, 100, __("Processing rows…"));

                    const file_name = values.csv_file.split("/").pop();

                    return frappe.call({
                        method : "quantbit_erpx_rental_system.quantbit_erpx_rental_system"
                                 + ".doctype.traffic_fine.traffic_fine.import_rop_csv",
                        args   : { file_content: content, file_name: file_name },
                        freeze : true,
                        freeze_message: __("Importing fines and matching contracts…"),
                    });
                })
                .then(r => {
                    frappe.hide_progress();
                    if (!r || !r.message) return;

                    const s = r.message;
                    const has_errors = s.failed > 0;

                    let error_html = "";
                    if (s.errors && s.errors.length) {
                        error_html = `
                            <hr>
                            <b>Row Errors (first ${s.errors.length}):</b><br>
                            <ul>${s.errors.map(e => `<li>${e}</li>`).join("")}</ul>
                        `;
                    }

                    frappe.msgprint({
                        title    : __("ROP Import Complete"),
                        message  : `
                            <table class="table table-bordered" style="font-size:13px">
                              <tr><td>Total Rows in File</td><td><b>${s.total_rows}</b></td></tr>
                              <tr><td>Fines Imported</td>
                                  <td><b style="color:green">${s.imported}</b></td></tr>
                              <tr><td>Auto-Matched to Contract</td>
                                  <td><b style="color:blue">${s.auto_matched}</b></td></tr>
                              <tr><td>Duplicates Skipped</td>
                                  <td><b style="color:grey">${s.duplicates}</b></td></tr>
                              <tr><td>Failed Rows</td>
                                  <td><b style="color:${has_errors ? 'red' : 'green'}">${s.failed}</b></td></tr>
                              <tr><td>Batch ID</td>
                                  <td><code>${s.batch_id}</code></td></tr>
                            </table>
                            ${error_html}
                        `,
                        indicator: has_errors ? "orange" : "green",
                    });

                    // Refresh the list view to show new fines
                    if (cur_list) cur_list.refresh();

                    // Quick-filter to this batch
                    if (s.imported > 0) {
                        setTimeout(() => {
                            frappe.confirm(
                                `Import complete. Filter list to show only Batch <b>${s.batch_id}</b>?`,
                                () => frappe.set_route("List", "Traffic Fine",
                                    {"import_batch_id": s.batch_id})
                            );
                        }, 500);
                    }
                })
                .catch(err => {
                    frappe.hide_progress();
                    frappe.msgprint({
                        title    : __("Import Failed"),
                        message  : String(err),
                        indicator: "red",
                    });
                });
        },
    });

    d.show();
}
