/** @odoo-module **/
/* Copyright 2026 Roomdoo
   License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl). */

import {Component, onWillStart, useSubEnv} from "@odoo/owl";
import {Layout} from "@web/search/layout";
import {getDefaultConfig} from "@web/views/view";
import {registry} from "@web/core/registry";
import {useService} from "@web/core/utils/hooks";
import {useSetupAction} from "@web/webclient/actions/action_hook";

/**
 * A Channex screen, embedded as a full page action with Odoo's own control
 * panel around it.
 *
 * The URL is asked for on every mount instead of travelling inside the action:
 * it carries a token that Channex drops on first use, so an action restored
 * from the breadcrumb has to mint a new one.
 */
export class ChannexIframeAction extends Component {
  setup() {
    // A client action gets no view config, and Layout reads the control
    // panel it renders from there.
    useSubEnv({
      config: {
        ...getDefaultConfig(),
        ...this.env.config,
      },
    });
    useSetupAction();
    this.orm = useService("orm");
    const params = this.props.action.params || {};
    onWillStart(async () => {
      this.url = await this.orm.call("channel.channex.backend", "channex_iframe_url", [
        [params.backend_id],
        params.page,
      ]);
    });
  }
}

ChannexIframeAction.components = {Layout};
ChannexIframeAction.template = "connector_pms_channex.ChannexIframeAction";

registry.category("actions").add("channex_iframe", ChannexIframeAction);
