#!/usr/bin/env python3
from ryu.base import app_manager
from ryu.controller import ofp_event
from ryu.controller.handler import CONFIG_DISPATCHER, MAIN_DISPATCHER, set_ev_cls
from ryu.ofproto import ofproto_v1_3
from ryu.lib import hub
import json
import os
import time

class UIIntegratedController(app_manager.RyuApp):
    OFP_VERSIONS = [ofproto_v1_3.OFP_VERSION]

    def __init__(self, *args, **kwargs):
        super(UIIntegratedController, self).__init__(*args, **kwargs)
        self.datapaths = {}
        self.mode = os.environ.get("SDN_MODE", "dynamic")
        self.live_path = os.environ.get("SDN_LIVE_FILE", "results/live_telemetry.json")
        self.telemetry_events = []
        self.prev_pkts = 0
        self.prev_ts = time.time()
        self.packets = 0
        self.bytes = 0
        self.rate = 0
        self.high_rate_active = False
        self.primary_link_up = True
        self.high_rate_pps = int(os.environ.get("SDN_HIGH_RATE_PPS", "1500"))
        self.low_rate_pps = int(os.environ.get("SDN_LOW_RATE_PPS", "1000"))
        self.current_out_port = 2  # 2 = Primary (s3), 3 = Backup (s2)
        self.measurement_rule_installed = False

        self._write_live()
        self.monitor_thread = hub.spawn(self._monitor)

    def _add_event(self, ev_type, detail):
        self.telemetry_events.append({"t": round(time.time(), 2), "event": ev_type, "detail": detail})
        self._write_live()

    def _write_live(self):
        parent = os.path.dirname(self.live_path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        tmp = self.live_path + ".tmp"
        with open(tmp, "w") as f:
            json.dump({
                "packets": self.packets,
                "bytes": self.bytes,
                "rate": self.rate,
                "events": self.telemetry_events
            }, f)
        os.replace(tmp, self.live_path)

    def add_flow(self, dp, priority, match, actions):
        ofp = dp.ofproto
        parser = dp.ofproto_parser
        inst = [parser.OFPInstructionActions(ofp.OFPIT_APPLY_ACTIONS, actions)]
        mod = parser.OFPFlowMod(datapath=dp, priority=priority, match=match, instructions=inst)
        dp.send_msg(mod)

    @set_ev_cls(ofp_event.EventOFPSwitchFeatures, CONFIG_DISPATCHER)
    def switch_features_handler(self, ev):
        dp = ev.msg.datapath
        self.datapaths[dp.id] = dp
        parser = dp.ofproto_parser

        # Start from a clean table so a reconnecting switch never keeps stale rules.
        ofp = dp.ofproto
        dp.send_msg(parser.OFPFlowMod(datapath=dp, command=ofp.OFPFC_DELETE, table_id=ofp.OFPTT_ALL,
                                      out_port=ofp.OFPP_ANY, out_group=ofp.OFPG_ANY, match=parser.OFPMatch()))

        if dp.id == 1:
            # Count UDP measurement flow from h1 -> h2 at priority 100 on s1.
            # The flag is reset because the rule was just deleted (MODIFY_STRICT
            # on a missing flow is a no-op in OpenFlow 1.3).
            self.measurement_rule_installed = False
            self.install_s1_measurement_rule(dp, out_port=2)
            # Return traffic on s1
            self.add_flow(dp, 50, parser.OFPMatch(in_port=2), [parser.OFPActionOutput(1)])
            self.add_flow(dp, 50, parser.OFPMatch(in_port=3), [parser.OFPActionOutput(1)])
            # ARP from h1 goes over both paths (no loop: s2/s3 only forward towards h2),
            # so ARP still resolves while the primary link is down.
            self.add_flow(dp, 60, parser.OFPMatch(eth_type=0x0806, in_port=1),
                          [parser.OFPActionOutput(2), parser.OFPActionOutput(3)])
            # Non-measured traffic (e.g. ping, TCP) from h1 uses the primary path.
            self.add_flow(dp, 30, parser.OFPMatch(in_port=1), [parser.OFPActionOutput(2)])
        elif dp.id == 2:
            # Backup switch s2: port 1 (from s1) <-> port 2 (to s3)
            self.add_flow(dp, 50, parser.OFPMatch(in_port=1), [parser.OFPActionOutput(2)])
            self.add_flow(dp, 50, parser.OFPMatch(in_port=2), [parser.OFPActionOutput(1)])
        elif dp.id == 3:
            # Edge switch s3: port 2 (from s1) or port 3 (from s2) -> port 1 (h2)
            self.add_flow(dp, 50, parser.OFPMatch(in_port=2), [parser.OFPActionOutput(1)])
            self.add_flow(dp, 50, parser.OFPMatch(in_port=3), [parser.OFPActionOutput(1)])
            self.add_flow(dp, 50, parser.OFPMatch(in_port=1), [parser.OFPActionOutput(2)])
            # ARP replies from h2 go back over both paths so they survive a primary failure.
            self.add_flow(dp, 60, parser.OFPMatch(eth_type=0x0806, in_port=1),
                          [parser.OFPActionOutput(2), parser.OFPActionOutput(3)])

    def install_s1_measurement_rule(self, dp, out_port):
        parser = dp.ofproto_parser
        ofp = dp.ofproto
        # Using OFPFC_MODIFY_STRICT so packet_count and byte_count are NEVER reset during reroute!
        match = parser.OFPMatch(in_port=1, eth_type=0x0800, ip_proto=17, ipv4_dst="10.0.0.2")
        actions = [parser.OFPActionOutput(out_port)]
        inst = [parser.OFPInstructionActions(ofp.OFPIT_APPLY_ACTIONS, actions)]
        cmd = ofp.OFPFC_MODIFY_STRICT if self.measurement_rule_installed else ofp.OFPFC_ADD
        mod = parser.OFPFlowMod(datapath=dp, command=cmd, priority=100, match=match, instructions=inst)
        dp.send_msg(mod)
        self.measurement_rule_installed = True
        self.current_out_port = out_port

    @set_ev_cls(ofp_event.EventOFPPortStatus, MAIN_DISPATCHER)
    def port_status_handler(self, ev):
        try:
            self._handle_port_status(ev)
        except Exception:
            self.logger.exception("port status handler failed")

    def _handle_port_status(self, ev):
        msg = ev.msg
        dp = msg.datapath
        if dp.id != 1 or msg.desc.port_no != 2:
            return

        ofp = dp.ofproto
        is_down = (msg.desc.state & ofp.OFPPS_LINK_DOWN) or (msg.desc.config & ofp.OFPPC_PORT_DOWN)

        if is_down:
            if self.primary_link_up:
                self.primary_link_up = False
                self._add_event("link_down", "Primary path s1:2 -> s3:2 failed")
            if self.mode == "dynamic" and self.current_out_port != 3:
                self.install_s1_measurement_rule(dp, out_port=3)
                self._add_event("reroute", "FAILOVER: measurement flow redirected to backup path via s2")
        else:
            if not self.primary_link_up:
                self.primary_link_up = True
                self._add_event("link_up", "Primary path s1:2 -> s3:2 recovered")
            if self.mode == "dynamic" and self.current_out_port == 3 and not self.high_rate_active:
                self.install_s1_measurement_rule(dp, out_port=2)
                self._add_event("recover", "RECOVERY: measurement flow returned to primary path")

    def _monitor(self):
        while True:
            if 1 in self.datapaths:
                dp = self.datapaths[1]
                parser = dp.ofproto_parser
                match = parser.OFPMatch(in_port=1, eth_type=0x0800, ip_proto=17, ipv4_dst="10.0.0.2")
                req = parser.OFPFlowStatsRequest(dp, 0, dp.ofproto.OFPTT_ALL, dp.ofproto.OFPP_ANY, dp.ofproto.OFPG_ANY, 0, 0, match)
                dp.send_msg(req)
            hub.sleep(0.5)

    @set_ev_cls(ofp_event.EventOFPFlowStatsReply, MAIN_DISPATCHER)
    def flow_stats_reply_handler(self, ev):
        try:
            self._handle_flow_stats(ev)
        except Exception:
            # An uncaught exception would kill Ryu's event loop for this app
            # (no more stats, no more rerouting), so log it and carry on.
            self.logger.exception("flow stats handler failed")

    def _handle_flow_stats(self, ev):
        dp = ev.msg.datapath  # was undefined before -> NameError on the first redirect
        now = time.time()
        for stat in ev.msg.body:
            if stat.priority == 100:
                dt = max(0.001, now - self.prev_ts)
                if dt >= 0.8:
                    self.rate = max(0, int(round((stat.packet_count - self.prev_pkts) / dt)))
                    self.prev_pkts = stat.packet_count
                    self.prev_ts = now

                    # Adaptive high-rate path response: redirect the measured flow to
                    # the alternate path when the observed packet rate is high.
                    if self.mode == "dynamic":
                        if self.rate > self.high_rate_pps and not self.high_rate_active:
                            self.high_rate_active = True
                            self._add_event("high_rate_detected", f"Traffic rate {self.rate} pps exceeded {self.high_rate_pps} pps policy threshold; selecting backup path")
                            if self.current_out_port == 2 and self.primary_link_up:
                                self.install_s1_measurement_rule(dp, out_port=3)
                                self._add_event("high_rate_redirect", "High-rate policy: primary -> backup via s2")
                        elif self.rate < self.low_rate_pps and self.high_rate_active:
                            self.high_rate_active = False
                            self._add_event("high_rate_cleared", f"Traffic normalized to {self.rate} pps")
                            if self.primary_link_up and self.current_out_port == 3:
                                self.install_s1_measurement_rule(dp, out_port=2)
                                self._add_event("high_rate_recovery", "Traffic returned to primary path")

                self.packets = stat.packet_count
                self.bytes = stat.byte_count
                self._write_live()
