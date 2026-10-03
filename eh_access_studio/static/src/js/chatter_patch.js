/** @odoo-module **/
// Copyright 2026 ERP Heritage (https://www.erpheritage.com.au)
// License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl-3.0).
//
// User Access Studio: hide Send Message / Log Note / Activities
// buttons on the chatter when an active profile says so. The whole
// chatter widget is stripped from arch in _get_view when hide_chatter
// is on; this patch handles the per-button case.

import { Chatter } from "@mail/chatter/web_portal/chatter";
import { patch } from "@web/core/utils/patch";
import { session } from "@web/session";
import { useService } from "@web/core/utils/hooks";
import { onWillStart, useState } from "@odoo/owl";

patch(Chatter.prototype, {
    setup() {
        super.setup();
        this.orm = useService("orm");
        this.ehAccess = useState({
            hide_send_message: false,
            hide_log_note: false,
            hide_schedule_activity: false,
        });
        // The answer normally travels with the session, so the chatter
        // mounts with no round trip at all and the buttons never paint
        // for a frame before the answer arrives. The call below is the
        // fallback for a session that predates this key.
        const published = session.eh_access_studio;
        if (published) {
            const model = this.props.threadModel;
            Object.assign(
                this.ehAccess,
                published.global || {},
                (published.models || {})[model] || {}
            );
            return;
        }
        onWillStart(async () => {
            const model = this.props.threadModel;
            if (!model) {
                return;
            }
            try {
                // @api.model RPC: pass the method's positional args
                // directly. Odoo's call_kw forwards them as
                // method(recordset, *args, **kwargs).
                const result = await this.orm.call(
                    "eh.access.profile",
                    "get_chatter_button_visibility",
                    [model],
                );
                Object.assign(this.ehAccess, result);
            } catch (err) {
                // Silent fallback so a configuration error never breaks
                // the chatter for everyone. The buttons remain visible.
                console.warn("Access Studio: chatter visibility lookup failed", err);
            }
        });
    },
});
