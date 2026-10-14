/** @odoo-module **/

import { patch } from "@web/core/utils/patch";
import { Chatter } from "@mail/chatter/web_portal/chatter";
import { onWillStart } from "@odoo/owl";

patch(Chatter.prototype, {
    setup() {
        super.setup(...arguments);
        onWillStart(this.onWillStart);
    },

    async onWillStart() {
        try {
            const access = await this.orm.call(
                this.props.threadModel,
                "get_chatter_data",
                [],
                {}
            );

            if (access) {
                Object.assign(this.state, access);
            }
        } catch (error) {
            // Ignore "method does not exist" errors.
            if (
                error?.data?.name === "builtins.AttributeError" ||
                error?.message?.includes("does not exist")
            ) {
                return;
            }

            throw error;
        }
    },
});