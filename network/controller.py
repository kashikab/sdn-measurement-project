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
        self.events = []
        self.prev_pkts = 0
        self.prev_ts = time.time()
        self.packets = 0
        self.bytes = 0
        self.rate = 0
        self.rate_limited = False
        self.current_out_port = 2  # 2 = Primary (s3), 3 = Backup (s2)

        self._write_live()
        self.monitor_thread = hub.spawn(self._monitor)

    def _add_event(self, ev_type, detail):
        self.events.append({"t": round(time.time(), 2), "event": ev_type, "detail": detail})
        self._write_live()

    def _write_live(self):
        os.makedirs(os.path.dirname(self.live_path), exist_ok=True)
        tmp = self.live_path + ".tmp"
        with open(tmp, "w") as f:
            json.dump({
                "packets": self.packets,
                "bytes": self.bytes,
                "rate": self.rate,
                "events": self.events
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

        if dp.id == 1:
            # Count UDP measurement flow from h1 -> h2 at priority 100 on s1
            self.install_s1_measurement_rule(dp, out_port=2)
            # Return traffic & ARP on s1
            self.add_flow(dp, 50, parser.OFPMatch(in_port=2), [parser.OFPActionOutput(1)])
            self.add_flow(dp, 50, parser.OFPMatch(in_port=3), [parser.OFPActionOutput(1)])
            self.add_flow(dp, 40, parser.OFPMatch(eth_type=0x0806, in_port=1), [parser.OFPActionOutput(2)])
        elif dp.id == 2:
            # Backup switch s2: port 1 (from s1) <-> port 2 (to s3)
            self.add_flow(dp, 50, parser.OFPMatch(in_port=1), [parser.OFPActionOutput(2)])
            self.add_flow(dp, 50, parser.OFPMatch(in_port=2), [parser.OFPActionOutput(1)])
        elif dp.id == 3:
            # Edge switch s3: port 2 (from s1) or port 3 (from s2) -> port 1 (h2)
            self.add_flow(dp, 50, parser.OFPMatch(in_port=2), [parser.OFPActionOutput(1)])
            self.add_flow(dp, 50, parser.OFPMatch(in_port=3), [parser.OFPActionOutput(1)])
            self.add_flow(dp, 50, parser.OFPMatch(in_port=1), [parser.OFPActionOutput(2)])

    def install_s1_measurement_rule(self, dp, out_port):
        parser = dp.ofproto_parser
        ofp = dp.ofproto
        # Using OFPFC_MODIFY_STRICT so packet_count and byte_count are NEVER reset during reroute!
        match = parser.OFPMatch(in_port=1, eth_type=0x0800, ip_proto=17, ipv4_dst="10.0.0.2")
        actions = [parser.OFPActionOutput(out_port)]
        inst = [parser.OFPInstructionActions(ofp.OFPIT_APPLY_ACTIONS, actions)]
        cmd = ofp.OFPFC_ADD if self.packets == 0 else ofp.OFPFC_MODIFY_STRICT
        mod = parser.OFPFlowMod(datapath=dp, command=cmd, priority=100, match=match, instructions=inst)
        dp.send_msg(mod)
        self.current_out_port = out_port

    @set_ev_cls(ofp_event.EventOFPPortStatus, MAIN_DISPATCHER)
    def port_status_handler(self, ev):
        msg = ev.msg
        dp = msg.datapath
        if dp.id != 1 or msg.desc.port_no != 2:
            return

        ofp = dp.ofproto
        is_down = (msg.desc.state & ofp.OFPPS_LINK_DOWN) or (msg.desc.config & ofp.OFPPC_PORT_DOWN)

        if is_down and self.current_out_port == 2:
            self._add_event("link_down", "s1 port 2 down")
            if self.mode == "dynamic":
                self.install_s1_measurement_rule(dp, out_port=3)
                self._add_event("reroute", "s1 measurement rule output 2 -> 3")
        elif not is_down and (
            any(e["event"] == "link_down" for e in self.events)
            and not any(e["event"] == "link_up" for e in self.events)
        ):
            self._add_event("link_up", "s1 port 2 up")
            if self.mode == "dynamic" and self.current_out_port == 3:
                self.install_s1_measurement_rule(dp, out_port=2)
                self._add_event("recover", "s1 measurement rule output 3 -> 2")

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
        now = time.time()
        for stat in ev.msg.body:
            if stat.priority == 100:
                dt = max(0.001, now - self.prev_ts)
                if dt >= 0.8:
                    self.rate = max(0, int(round((stat.packet_count - self.prev_pkts) / dt)))
                    self.prev_pkts = stat.packet_count
                    self.prev_ts = now

                    # UNLISTED FEATURE: Dynamic Anomaly Rate-Limit Guard
                    if self.mode == "dynamic":
                        if self.rate > 1500 and not self.rate_limited:
                            self.rate_limited = True
                            self._add_event("rate_limit_on", f"High UDP burst ({self.rate} pps > 1500 pps threshold)")
                        elif self.rate <= 1500 and self.rate_limited:
                            self.rate_limited = False
                            self._add_event("rate_limit_off", "Traffic normalized below threshold")

                self.packets = stat.packet_count
                self.bytes = stat.byte_count
                self._write_live()
