// Copyright (c) 2026, Quantbit Technologies Pvt. Ltd. and contributors
// For license information, please see license.txt

frappe.ui.form.on("PDC Register", {

    customer(frm) {
        frm.set_query("rental_contract", () => {
            if (!frm.doc.customer) return {};
            return {
                query: "quantbit_erpx_rental_system.quantbit_erpx_rental_system.doctype.pdc_register.pdc_register.get_rental_contracts_for_customer",
                filters: { customer: frm.doc.customer }
            };
        });
        if (frm.doc.rental_contract) {
            frm.set_value("rental_contract", "");
        }
    },

    refresh(frm) {
        // Restore rental_contract query on load/edit
        if (frm.doc.customer) {
            frm.set_query("rental_contract", () => ({
                query: "quantbit_erpx_rental_system.quantbit_erpx_rental_system.doctype.pdc_register.pdc_register.get_rental_contracts_for_customer",
                filters: { customer: frm.doc.customer }
            }));
        }

        // When status is Submitted to Bank, ensure bank submission fields are visible
        _toggle_bank_fields(frm);
    },

    pdc_status(frm) {
        _toggle_bank_fields(frm);
    }

});

function _toggle_bank_fields(frm) {
    const is_submitted_to_bank = frm.doc.pdc_status === "Submitted to Bank";

    // Make fields visible and editable when status is Submitted to Bank
    ["date_submitted_to_bank", "submitted_by", "bank_reference_number"].forEach(field => {
        frm.set_df_property(field, "hidden", 0);          // always show them
        frm.set_df_property(field, "read_only", frm.doc.docstatus === 2 ? 1 : 0); // lock only if cancelled
    });

    // Expand the Collection Details section so fields aren't hidden behind collapse
    frm.set_df_property("section_collection", "collapsible", 0);

    frm.refresh_fields(["date_submitted_to_bank", "submitted_by", "bank_reference_number", "section_collection"]);
}