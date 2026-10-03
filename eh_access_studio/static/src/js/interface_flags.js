/** @odoo-module **/
// Copyright 2026 ERP Heritage (https://www.erpheritage.com.au)
// License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl-3.0).
//
// User Access Studio: publish the profile's interface switches as
// classes on the document root, which interface_flags.scss acts on.
//
// This replaced two template patches. The search menu is drawn by
// different components on every series, and on Odoo 19 the same entry
// exists twice (the field picker and the properties picker), so an
// xpath on core's internals hid one of them on one series and threw on
// another. A class on the root and a selector list is version tolerant,
// and every switch it covers is enforced at the server as well, except
// the two search entries, which are interface rules by nature.
//
// The flags travel with the session (ir_http.session_info), so there is
// no round trip; the call below is the fallback for a session that
// predates that key.

import { registry } from "@web/core/registry";
import { session } from "@web/session";

const FLAG_CLASSES = {
    hide_send_message: "eh_access_hide_send_message",
    hide_log_note: "eh_access_hide_log_note",
    hide_schedule_activity: "eh_access_hide_schedule_activity",
    hide_custom_filter: "eh_access_hide_custom_filter",
    hide_custom_group: "eh_access_hide_custom_group",
};

function applyFlags(flags) {
    // documentElement, not body: services start before the body exists
    // on Odoo 16.
    const root = document.documentElement;
    for (const [flag, className] of Object.entries(FLAG_CLASSES)) {
        root.classList.toggle(className, Boolean(flags[flag]));
    }
}

export const ehAccessStudioInterfaceService = {
    dependencies: ["orm"],
    async start(env, { orm }) {
        const published = session.eh_access_studio;
        if (published) {
            applyFlags(published.global || {});
            return;
        }
        try {
            applyFlags(await orm.call(
                "eh.access.profile", "get_interface_flags", []
            ));
        } catch (err) {
            // A configuration error must never break the client. The
            // entries stay visible; the server still refuses the action.
            console.warn("Access Studio: interface flag lookup failed", err);
        }
    },
};

registry.category("services").add(
    "eh_access_studio_interface", ehAccessStudioInterfaceService
);
