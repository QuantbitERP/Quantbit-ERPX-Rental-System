// Copyright (c) 2026, Quantbit Technologies Pvt. Ltd. and contributors
// For license information, please see license.txt
frappe.ui.form.on('Invoice Schedule', {

    refresh(frm) {

        if (frm.doc.schedule_status === "Active") {  // only if active

            frm.add_custom_button('Generate Invoice', () => {

                frappe.call({
                    method: "create_invoice",  // call python method
                    doc: frm.doc,
                    callback: function (r) {
                        if (r.message) {
                            frappe.msgprint("Invoice Created: " + r.message);
                            frm.reload_doc();
                        }
                    }
                });

            });

        }

    }

});
