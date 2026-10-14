// @odoo-module

import { registry } from "@web/core/registry";
import { patch } from "@web/core/utils/patch";
import { rpc } from "@web/core/network/rpc";
import { router } from "@web/core/browser/router";
import { browser } from "@web/core/browser/browser";
import { session } from "@web/session";


patch(registry.category("services").get("view"), {
    start(env, { orm }) {
        super.start(...arguments)

        async function loadViews(params, options = {}) {
            const { context, resModel, views } = params;
            const loadViewsOptions = {
                action_id: options.actionId || false,
                embedded_action_id: options.embeddedActionId || false,
                embedded_parent_res_id: options.embeddedParentResId || false,
                load_filters: options.loadIrFilters || false,
                toolbar: (!context?.disable_toolbar && options.loadActionMenus) || false,
            };
            for (const key in options) {
                if (
                    ![
                        "actionId",
                        "embeddedActionId",
                        "embeddedParentResId",
                        "loadIrFilters",
                        "loadActionMenus",
                    ].includes(key)
                ) {
                    loadViewsOptions[key] = options[key];
                }
            }
            if (env.isSmall) {
                loadViewsOptions.mobile = true;
            }
            if (env.debug) {
                loadViewsOptions.debug = true;
            }
            const filteredContext = Object.fromEntries(
                Object.entries(context || {}).filter(
                    ([k, v]) => k == "lang" || k.endsWith("_view_ref")
                )
            );

            const result = await orm.call(resModel, "get_views", [], {
                context: filteredContext,
                views,
                options: loadViewsOptions,
            });

            // Check debug mode restriction
            const is_debug_restricted = await rpc("/restrict_debug/check", { model_name: resModel });
            if (is_debug_restricted) {
                // For Odoo-debug extension from Droggol
                const body = document.getElementsByTagName('body')[0];
                body.setAttribute('data-odoo-debug-mode', '')

                // Keeps debug mode deactivated
                router.hideKeyFromUrl('debug')
                browser.location.search
                const url = new URL(browser.location)
                const state = router.urlToState(url)
                state.debug = 0
                router.replaceState(state);
                odoo.debug = ''
                session.bundle_params.debug = ''
            }

            const viewDescriptions = {
                fields: result.models[resModel].fields,
                relatedModels: result.models,
                views: {},
            };
            
            for (const viewType in result.views) {
                const { arch, toolbar, id, filters, custom_view_id } = result.views[viewType];
                const viewDescription = { arch, id, custom_view_id };
                if (toolbar) {
                    viewDescription.actionMenus = toolbar;
                }
                if (filters) {
                    viewDescription.irFilters = filters;
                }
                viewDescriptions.views[viewType] = viewDescription;
            }
            
            return viewDescriptions;
        }
        
        return { loadViews }
    }
})
