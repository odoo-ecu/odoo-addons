/** @odoo-module **/

import { ListController } from "@web/views/list/list_controller";
import { patch } from "@web/core/utils/patch";
import { onWillStart } from "@odoo/owl";
import { useService } from "@web/core/utils/hooks";

patch(ListController.prototype, {
    setup() {
        super.setup(...arguments)
        this.orm = useService('orm');
        onWillStart(async () => {
            this.isExportEnable = await this.isExportEnable();
        });
    },

    async isExportEnable() {
        return await this.orm.call('res.users', 'check_export_enable', [this.model.config.resModel], {})
    }
})