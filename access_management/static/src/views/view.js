/** @odoo-module **/

import { View } from "@web/views/view";
import { patch } from "@web/core/utils/patch";
import { rpc } from "@web/core/network/rpc";
import { onWillStart } from "@odoo/owl";

patch(View.prototype, {
    setup() {
        super.setup(...arguments)

        onWillStart(async () => {
            const result = await rpc('/check_restricted_views', { model: this.props.resModel })
            if (result.length) {
                const views = this.env.config.views.filter(v => !result.includes(v[1]))
                this.env.config.views = views
                this.env.config.viewSwitcherEntries = this.env.config.viewSwitcherEntries.filter(v => views.map(v1 => v1[1]).includes(v.type))
            }
        })
    }
})