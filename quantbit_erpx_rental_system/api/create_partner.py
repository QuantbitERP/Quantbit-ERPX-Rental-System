import frappe


@frappe.whitelist(allow_guest=True, xss_safe=True)
def create_partner(
    supplier_name=None,
    supplier_type=None,
    email=None,
    mobile=None,
    gst=None,
    territory=None,
    commission_rate=None,
    address=None,
    address_line2=None,
    city=None,
    state=None,
    pincode=None,
    country=None,
    address_type=None,
):
    """
    Quantbit Technologies — Partner Registration API
    Called from partner.html
    Creates: Supplier + Sales Partner + their Addresses
    """
    try:
        # ── 1. Load Partner Registration Settings ────────────────
        settings = frappe.get_single("Partner Registration Settings")

        if not settings.api_key:
            return {
                "status": "error",
                "message": "API credentials not configured. Contact administrator.",
            }

        # ── 2. Authenticate using the stored API key ─────────────
        api_user = frappe.db.get_value("User", {"api_key": settings.api_key}, "name")
        if not api_user:
            return {"status": "error", "message": "Invalid API key in settings."}
        frappe.set_user(api_user)

        # ── 3. Commission rate — form value takes priority, fallback to settings ──
        try:
            commission_rate = float(commission_rate) if commission_rate else float(settings.partners_commission_rate or 0)
        except (ValueError, TypeError):
            commission_rate = 0.0

        # ── 4. Territory fallback ─────────────────────────────────
        territory = territory or "All Territories"

        # ── 5. Address type fallback ──────────────────────────────
        address_type = address_type or "Billing"

        # ── 6. Country fallback ───────────────────────────────────
        country = country or "India"

        # ── 7. Basic input validation ─────────────────────────────
        if not supplier_name:
            return {"status": "error", "message": "Supplier name is required."}
        if not email:
            return {"status": "error", "message": "Email is required."}
        if not mobile:
            return {"status": "error", "message": "Mobile number is required."}

        # ── 8. Duplicate supplier check ───────────────────────────
        if frappe.db.exists("Supplier", {"supplier_name": supplier_name}):
            return {
                "status": "error",
                "message": f"A supplier named '{supplier_name}' already exists. Please use a different name.",
            }

        # ── 9. Duplicate Sales Partner check ─────────────────────
        if frappe.db.exists("Sales Partner", {"partner_name": supplier_name}):
            return {
                "status": "error",
                "message": f"A sales partner named '{supplier_name}' already exists. Please use a different name.",
            }

        # ── 10. Create Supplier ───────────────────────────────────
        supplier = frappe.get_doc({
            "doctype": "Supplier",
            "supplier_name": supplier_name,
            "supplier_type": supplier_type or "Company",
            "gstin": gst or "",
            "email_id": email,
            "mobile_no": mobile,
        })
        supplier.insert(ignore_permissions=True)

        # ── 11. Supplier Address ──────────────────────────────────
        if address:
            supplier_addr = frappe.get_doc({
                "doctype": "Address",
                "address_title": supplier_name,
                "address_type": address_type,
                "address_line1": address,
                "address_line2": address_line2 or "",
                "city": city or "",
                "state": state or "",
                "pincode": pincode or "",
                "country": country,
                "email_id": email,
                "phone": mobile,
                "links": [
                    {
                        "link_doctype": "Supplier",
                        "link_name": supplier.name,
                    }
                ],
            })
            supplier_addr.insert(ignore_permissions=True)

        # ── 12. Create Sales Partner ──────────────────────────────
        sales_partner = frappe.get_doc({
            "doctype": "Sales Partner",
            "partner_name": supplier_name,
            "email_id": email,
            "mobile_no": mobile,
            "commission_rate": commission_rate,
            "territory": territory,
        })
        sales_partner.insert(ignore_permissions=True)

        # ── 13. Sales Partner Address ─────────────────────────────
        if address:
            sp_addr = frappe.get_doc({
                "doctype": "Address",
                "address_title": supplier_name + " - SP",
                "address_type": address_type,
                "address_line1": address,
                "address_line2": address_line2 or "",
                "city": city or "",
                "state": state or "",
                "pincode": pincode or "",
                "country": country,
                "email_id": email,
                "phone": mobile,
                "links": [
                    {
                        "link_doctype": "Sales Partner",
                        "link_name": sales_partner.name,
                    }
                ],
            })
            sp_addr.insert(ignore_permissions=True)

        frappe.db.commit()

        return {
            "status": "success",
            "supplier": supplier.name,
            "sales_partner": sales_partner.name,
            "message": (
                f"Welcome to Quantbit Technologies Partner Network!\n\n"
                f"Supplier: {supplier.name}\n"
                f"Sales Partner: {sales_partner.name}\n\n"
                f"Our team will contact you within 24 hours. 🚀"
            ),
        }

    except frappe.exceptions.DuplicateEntryError:
        frappe.db.rollback()
        return {
            "status": "error",
            "message": f"Partner '{supplier_name}' already exists in our system.",
        }
    except Exception as e:
        frappe.db.rollback()
        frappe.log_error(frappe.get_traceback(), "Partner Registration Error")
        return {"status": "error", "message": str(e)}