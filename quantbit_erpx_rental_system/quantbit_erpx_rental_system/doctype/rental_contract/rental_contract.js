// Copyright (c) 2026, Quantbit Technologies Pvt. Ltd. and contributors
// For license information, please see license.txt


frappe.ui.form.on('Rental Contract', {

    vehicle: function(frm) {
        if (frm.doc.vehicle) {
            frappe.db.get_value('Vehicle Master', frm.doc.vehicle, 'vehicle_status')
                .then(r => {
                    if (r.message) {
                        frm.set_value('vehicle_status_at_contract', r.message.vehicle_status);
                    }
                });
        }
    }

});
