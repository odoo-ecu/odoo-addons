/** @odoo-module **/

import { patch } from "@web/core/utils/patch";
import { ImportRecords } from "@base_import/import_records/import_records";
import { useService } from "@web/core/utils/hooks";
import { onWillStart } from "@odoo/owl";

patch(ImportRecords.prototype, {
    setup() {
        super.setup(...arguments)
        this.orm = useService('orm')
        onWillStart(this.hideImportRecords)
    },

    async hideImportRecords() {
        this.hideImportRecords = await this.orm.call('res.users', 'check_import_enabled', [this.env.model.config.resModel], {})
    }
})
