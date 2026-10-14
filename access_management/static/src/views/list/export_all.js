/** @odoo-module **/

import { ExportAll } from "@web/views/list/export_all/export_all";
import { user } from "@web/core/user";
import { exprToBoolean } from "@web/core/utils/strings";
import { STATIC_ACTIONS_GROUP_NUMBER } from "@web/search/action_menus/action_menus";

export const exportAllItem = {
    Component: ExportAll,
    groupNumber: STATIC_ACTIONS_GROUP_NUMBER,

    // Add extra condition here
    isDisplayed: async (env) =>
        ["kanban", "list"].includes(env.config.viewType) &&
        !env.model.root.selection.length &&
        (await user.hasGroup("base.group_allow_export")) &&
        exprToBoolean(env.config.viewArch.getAttribute("export_xlsx"), true)
    // 👇 Your custom condition here (example: context-based or xattrs)
    // (env.config.context.allow_custom_export === true ||
    //     exprToBoolean(env.config.viewArch.getAttribute("enable_custom_export"), false)),
};
