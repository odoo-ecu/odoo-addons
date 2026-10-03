/** @odoo-module **/
/* ERP Heritage - User Access Studio
 * Browser smoke test, run by Odoo's own headless Chrome as a restricted
 * user. Unit tests call model methods directly, so they cannot see a
 * defect in how the WEB CLIENT reaches them: an override that loses
 * @api.model makes call_kw read the domain as record ids and every list
 * view fails, with every server-side test still green. This tour walks
 * the screens a profile touches: the list loads, a hidden column is
 * absent, the search dropdown opens without throwing, a record opens,
 * and the chatter shows only the buttons the profile leaves. */

import { registry } from "@web/core/registry";

function assertOk(condition, message) {
    if (!condition) {
        throw new Error("eh_access_studio smoke: " + message);
    }
}

function buttonsText() {
    // VISIBLE buttons only: on Odoo 16 the profile's interface half is a
    // stylesheet, so a hidden button is still in the document.
    return Array.from(document.querySelectorAll("button, a.btn"))
        .filter((node) => node.offsetParent !== null)
        .map((node) => (node.textContent || "").trim());
}

registry.category("web_tour.tours").add("eh_access_studio_smoke", {
    steps: () => [
        {
            content: "the list view loads for a restricted user",
            trigger: ".o_list_view .o_data_row",
            run: () => {
                const headers = Array.from(
                    document.querySelectorAll(".o_list_view thead th")
                ).map((node) => node.getAttribute("data-name"));
                assertOk(
                    !headers.includes("email"),
                    "the hidden column is still in the list header"
                );
            },
        },
        {
            content: "the search dropdown builds without throwing",
            trigger: ".o_searchview_dropdown_toggler, .o_dropdown_toggler_btn, .o_searchview input",
            run: "click",
        },
        {
            content: "the custom filter and group entries the profile hides are gone",
            trigger: ".o_list_view",
            run: () => {
                // The stylesheet is asserted directly as well, because a
                // series whose search menu this tour cannot open would
                // otherwise pass the check by having nothing to find.
                for (const className of [
                    "o_add_custom_filter", "o_add_custom_filter_menu",
                    "o_add_custom_group_menu",
                ]) {
                    const probe = document.createElement("div");
                    probe.className = className;
                    document.body.appendChild(probe);
                    const display = getComputedStyle(probe).display;
                    probe.remove();
                    assertOk(
                        display === "none",
                        "the profile hides " + className + " but it renders as " + display
                    );
                }
                const vis = (node) => node && node.offsetParent !== null;
                // 17 and later: one search menu. 16: two dropdowns.
                const offenders = [
                    ".o_add_custom_filter",
                    ".o_add_custom_filter_menu",
                    ".o_add_custom_group_menu",
                ].filter((selector) =>
                    Array.from(document.querySelectorAll(selector)).some(vis)
                );
                assertOk(
                    !offenders.length,
                    "the search menu still offers " + offenders.join(", ")
                );
            },
        },
        {
            content: "back to the records, then open one",
            trigger: ".o_list_view .o_data_row .o_data_cell",
            run: "click",
        },
        {
            content: "the form renders",
            trigger: ".o_form_view .o_form_sheet",
            run: () => {
                assertOk(document.querySelector(".o_form_view .o_form_sheet"), "the contact form did not render");
            },
        },
        {
            content: "the chatter renders, minus the hidden parts",
            // Waiting on a chatter BUTTON, not the container: the chatter
            // mounts asynchronously and its top bar arrives after it.
            trigger: ".o-mail-Chatter button, .o_Chatter button, .o_ChatterTopbar button",
            run: () => {
                const chatter = document.querySelector(".o-mail-Chatter, .o_Chatter");
                assertOk(chatter, "the chatter did not render");
                const labels = buttonsText();
                assertOk(
                    !labels.some((text) => text === "Send message"),
                    "Send message is still offered under a profile that hides it"
                );
                assertOk(
                    labels.some((text) => text === "Log note"),
                    "Log note was removed although the profile keeps it"
                );
                const fields = Array.from(
                    document.querySelectorAll(".o_form_view [name]")
                ).map((node) => node.getAttribute("name"));
                assertOk(
                    !fields.includes("email"),
                    "the hidden field is still on the form"
                );
            },
        },
    ],
});

// Native v3 forms: these tours deliberately use their rendered fields and
// object buttons. They do not replace views or call a model service directly.
registry.category("web_tour.tours").add("eh_access_studio_overview", {
    steps: () => [
        {
            content: "the manager overview renders its operational counts",
            trigger: ".o_form_view [name='pending_count']",
            run: () => {
                for (const name of ["pending_count", "expiring_count", "overdue_count", "draft_count", "role_count", "audit_ids"]) {
                    assertOk(document.querySelector(`.o_form_view [name='${name}']`), `${name} is missing from Overview`);
                }
                assertOk(
                    document.querySelector(".o_form_view").textContent.includes("scheduler"),
                    "Overview omitted the scheduler limitation"
                );
            },
        },
    ],
});

registry.category("web_tour.tours").add("eh_access_studio_inspector", {
    steps: () => [
        {
            content: "the inspector shows the saved question and native record picker",
            trigger: ".o_form_view button[name='action_inspect']",
            run: () => {
                for (const name of ["user_id", "model_id", "operation", "company_id", "record_id"]) {
                    assertOk(document.querySelector(`.o_form_view [name='${name}']`), `${name} is missing from the inspector`);
                }
            },
        },
        {
            content: "run the read-only access inspection through its form button",
            trigger: ".o_form_view button[name='action_inspect']",
            run: "click",
        },
        {
            content: "the result renders explanatory tables and limits",
            trigger: ".o_form_view [name='result_html'] table",
            run: () => {
                const result = document.querySelector(".o_form_view [name='result_html']").textContent;
                for (const text of ["Access checks passed", "User and scope", "Effective Odoo groups", "Limits of this result"]) {
                    assertOk(result.includes(text), `the inspector omitted ${text}`);
                }
            },
        },
    ],
});

registry.category("web_tour.tours").add("eh_access_studio_ai_draft", {
    steps: () => [
        {
            content: "the optional AI form renders consent and separate draft actions",
            trigger: "button[name='action_generate']",
            run: () => {
                for (const name of ["name", "model_id", "prompt", "consent", "draft_json", "explanation"]) {
                    assertOk(document.querySelector(`.o_form_view [name='${name}']`), `${name} is missing from the AI draft form`);
                }
                const consent = document.querySelector(".o_form_view [name='consent'] input");
                assertOk(consent && !consent.checked, "AI transmission consent must not be preselected");
                assertOk(document.querySelector("button[name='action_create_draft']"), "the separate create-inactive-profile action is missing");
                assertOk(document.querySelector(".o_form_view").textContent.includes("inactive draft"), "the draft-only explanation is missing");
                // No provider request is made by this rendering test.
            },
        },
    ],
});

registry.category("web_tour.tours").add("eh_access_studio_employee_request", {
    steps: () => [
        {
            content: "the employee sees Access Requests without the manager Overview",
            trigger: ".o_menu_sections [data-menu-xmlid='eh_access_studio.menu_eh_access_requests']",
            run: () => {
                assertOk(
                    !document.querySelector("[data-menu-xmlid='eh_access_studio.menu_eh_access_overview']"),
                    "an ordinary employee sees the manager Overview menu"
                );
            },
        },
        {
            content: "only the employee's own request is listed",
            trigger: ".o_list_view .o_data_row",
            run: () => {
                const rows = document.querySelectorAll(".o_list_view .o_data_row");
                assertOk(rows.length === 1, "another employee's request is visible");
                assertOk(rows[0].textContent.includes("eh_governance_employee"), "the list did not show the signed-in employee's request");
            },
        },
        {
            content: "open the employee's own request form",
            trigger: ".o_list_view .o_data_row .o_data_cell",
            run: "click",
        },
        {
            content: "the request form exposes the employee workflow",
            trigger: ".o_form_view button[name='action_submit']",
            run: () => {
                for (const name of ["role_id", "reason", "starts_at", "ends_at"]) {
                    assertOk(document.querySelector(`.o_form_view [name='${name}']`), `${name} is missing from the request form`);
                }
                for (const name of ["action_approve", "action_reject", "action_revoke"]) {
                    assertOk(!document.querySelector(`.o_form_view button[name='${name}']`), `the employee has the manager ${name} control`);
                }
            },
        },
        {
            content: "submit the request using the employee's form button",
            trigger: ".o_form_view button[name='action_submit']",
            run: "click",
        },
        {
            content: "the request is submitted without granting access",
            // Use the server-authored submission timestamp, which is only
            // shown after submission. Statusbar rendering differs by series
            // and its selected arrow can lag behind the refreshed record.
            // The HttpCase also asserts the persisted state and no assignment.
            trigger: ".o_form_view [name='submitted_at']",
            run: () => {
                const submitted = document.querySelector(".o_form_view [name='submitted_at']");
                assertOk(submitted && submitted.textContent.trim(), "the submission timestamp did not render");
                const submit = document.querySelector(".o_form_view button[name='action_submit']");
                assertOk(!submit || submit.offsetParent === null, "the submitted request still offers Submit Request");
                assertOk(document.querySelector(".o_form_view button[name='action_cancel']"), "the employee cannot cancel a submitted request");
            },
        },
    ],
});
