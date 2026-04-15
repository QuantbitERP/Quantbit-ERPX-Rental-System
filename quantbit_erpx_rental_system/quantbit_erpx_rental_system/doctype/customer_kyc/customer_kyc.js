// Copyright (c) 2026, Quantbit Technologies Pvt. Ltd.

frappe.ui.form.on('Customer KYC', {

    refresh: function(frm) {
        toggle_fields(frm);
    },

    customer_type: function(frm) {
        toggle_fields(frm);
    },

    kyc_status: function(frm) {
        if (frm.doc.kyc_status === "Blacklisted" && !frm.doc.blacklist_reason) {
            frappe.msgprint("⚠️ Please select Blacklist Reason");
        }
    },

    customer: function(frm) {
        if (frm.doc.customer && !frm.doc.full_name) {
            frappe.db.get_value('Customer', frm.doc.customer, 'customer_name')
                .then(r => {
                    if (r.message) {
                        frm.set_value('full_name', r.message.customer_name);
                    }
                });
        }
    }

});




function toggle_fields(frm) {

    const is_corporate = ["Corporate", "Broker / Travel Agency"].includes(frm.doc.customer_type);
    const is_broker = frm.doc.customer_type === "Broker / Travel Agency";

    // corporate fields
    frm.toggle_display("company_name", is_corporate);
    frm.toggle_display("cr_number", is_corporate);
    frm.toggle_display("vat_registration_number", is_corporate);

    // broker fields
    frm.toggle_display("commission_type", is_broker);
    frm.toggle_display("commission_rate", is_broker);
    frm.toggle_display("commission_gl_account", is_broker);
}