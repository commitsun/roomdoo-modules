/** @odoo-module **/
/* Copyright 2026 Roomdoo
   License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl). */

import {Component, onWillStart, useState, useSubEnv} from "@odoo/owl";
import {View, getDefaultConfig} from "@web/views/view";
import {Layout} from "@web/search/layout";
import {registry} from "@web/core/registry";
import {useService} from "@web/core/utils/hooks";
import {useSetupAction} from "@web/webclient/actions/action_hook";

const CHANNELS_MODEL = "channel.channex.channel";

/**
 * The whole channel flow in one screen: connect them on Channex, then say who
 * each one is.
 *
 * A screen and not a wizard, because it has to be worth coming back to: the
 * hotel connects a new OTA months later, and Channex has no way of telling Odoo
 * that it happened. Landing on the mapping step already synchronised is what
 * makes that work without a step called "synchronise", which means nothing to a
 * hotelier.
 */
export class ChannexChannelsAction extends Component {
  setup() {
    // A client action gets no view config, and both Layout and View read what
    // they render from there.
    useSubEnv({
      config: {
        ...getDefaultConfig(),
        ...this.env.config,
      },
    });
    useSetupAction();
    this.orm = useService("orm");
    this.params = this.props.action.params || {};
    this.state = useState({
      step: this.params.step === "map" ? "map" : "connect",
      url: null,
      busy: false,
    });
    onWillStart(async () => {
      if (this.state.step === "map") {
        await this.sync();
      } else {
        await this.loadUrl();
      }
    });
  }

  get listProps() {
    return {
      resModel: CHANNELS_MODEL,
      type: "list",
      views: [[false, "list"]],
      domain: [["backend_id", "=", this.params.backend_id]],
      context: {},
      display: {controlPanel: false},
      allowSelectors: false,
    };
  }

  /**
   * Minted per load: Channex drops the token on first use, so coming back to
   * the first step needs a new one.
   */
  async loadUrl() {
    this.state.url = await this.orm.call(
      "channel.channex.backend",
      "channex_iframe_url",
      [[this.params.backend_id], "/channels"]
    );
  }

  async sync() {
    await this.orm.call("channel.channex.backend", "channex_sync_channels", [
      [this.params.backend_id],
    ]);
  }

  async toMap() {
    this.state.busy = true;
    try {
      await this.sync();
      this.state.step = "map";
    } finally {
      this.state.busy = false;
    }
  }

  async toConnect() {
    this.state.busy = true;
    try {
      await this.loadUrl();
      this.state.step = "connect";
    } finally {
      this.state.busy = false;
    }
  }
}

ChannexChannelsAction.components = {Layout, View};
ChannexChannelsAction.template = "connector_pms_channex.ChannexChannelsAction";

registry.category("actions").add("channex_channels", ChannexChannelsAction);
