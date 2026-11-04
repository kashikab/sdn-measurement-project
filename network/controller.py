#!/usr/bin/env python3
from ryu.base import app_manager
from ryu.controller import ofp_event
from ryu.controller.handler import CONFIG_DISPATCHER, MAIN_DISPATCHER, DEAD_DISPATCHER, set_ev_cls
from ryu.ofproto import ofproto_v1_3
from ryu.lib.packet import packet, ethernet, ether_types, ipv4, arp
from ryu.lib import hub
import time
import csv
import os

class PredictiveSDNController(app_manager.RyuApp):
    OFP_VERSIONS = [ofproto_v1_3.OFP_VERSION]

    def __init__(self, *args, **kwargs):
        super(PredictiveSDNController, self).__init__(*args, **kwargs)
        self.datapaths = {}
        self.prev_port_bytes = {}  # (dpid, port_no) -> (rx_bytes + tx_bytes, timestamp)
        self.prev_bw = {}          # (dpid, port_no) -> previous_mbps
        self.active_path = 2       # Port 2 = Path A (Primary), Port 3 = Path B (Backup)
        self.rerouted = False
        self.start_time = time.time()

        # Enable/Disable Dynamic Predictive Rerouting via environment variable for Baseline comparison
        self.dynamic_enabled = os.environ.get('SDN_DYNAMIC', '1') == '1'
        self.capacity_mbps = 10.0
        self.predictive_threshold_mbps = 7.5  # Preemptively reroute at 75% predicted load

        os.makedirs('results', exist_ok=True)
        self.csv_path = os.environ.get('SDN_CSV_PATH', 'results/sdn_controller_stats.csv')
        self.csv_file = open(self.csv_path, 'w', newline='')
        self.writer = csv.writer(self.csv_file)
        self.writer.writerow([
            'elapsed_sec', 'dpid', 'port_a_mbps', 'port_b_mbps',
            'total_sdn_mbps', 'derivative_mbps_s', 'predicted_next_mbps',
            'active_path', 'event'
        ])

        self.monitor_thread = hub.spawn(self._monitor_loop)

    @set_ev_cls(ofp_event.EventOFPStateChange, [MAIN_DISPATCHER, DEAD_DISPATCHER])
    def _state_change_handler(self, ev):
        dp = ev.datapath
        if ev.state == MAIN_DISPATCHER:
            self.datapaths[dp.id] = dp
            self.logger.info(f"[SDN] Switch s{dp.id} connected (Dynamic Mode: {self.dynamic_enabled})")
        elif ev.state == DEAD_DISPATCHER:
            self.datapaths.pop(dp.id, None)

    @set_ev_cls(ofp_event.EventOFPSwitchFeatures, CONFIG_DISPATCHER)
    def switch_features_handler(self, ev):
        dp = ev.msg.datapath
        ofp = dp.ofproto
        parser = dp.ofproto_parser

        # Table-miss rule
        match = parser.OFPMatch()
        actions = [parser.OFPActionOutput(ofp.OFPP_CONTROLLER, ofp.OFPCML_NO_BUFFER)]
        self.add_flow(dp, 0, match, actions)

        # Install static baseline forwarding rules for multipath topology to prevent loops
        self._install_base_topology_flows(dp)

    def add_flow(self, dp, priority, match, actions, idle_timeout=0):
        ofp = dp.ofproto
        parser = dp.ofproto_parser
        inst = [parser.OFPInstructionActions(ofp.OFPIT_APPLY_ACTIONS, actions)]
        mod = parser.OFPFlowMod(
            datapath=dp, priority=priority, idle_timeout=idle_timeout,
            match=match, instructions=inst
        )
        dp.send_msg(mod)

    def _install_base_topology_flows(self, dp):
        parser = dp.ofproto_parser
        dpid = dp.id

        # Host Map: IP -> (local_switch_dpid, host_port)
        # s1: h1 (10.0.0.1) on port 1, h3 (10.0.0.3) on port 4
        # s2: h2 (10.0.0.2) on port 1, h4 (10.0.0.4) on port 4
        if dpid == 1:
            # Local delivery on s1
            for ip, port in [('10.0.0.1', 1), ('10.0.0.3', 4)]:
                self.add_flow(dp, 10, parser.OFPMatch(eth_type=0x0800, ipv4_dst=ip), [parser.OFPActionOutput(port)])
                self.add_flow(dp, 10, parser.OFPMatch(eth_type=0x0806, arp_tpa=ip), [parser.OFPActionOutput(port)])
            # Remote delivery to s2 (Default via Path A = port 2)
            for ip in ['10.0.0.2', '10.0.0.4']:
                self.add_flow(dp, 10, parser.OFPMatch(eth_type=0x0800, ipv4_dst=ip), [parser.OFPActionOutput(2)])
                self.add_flow(dp, 10, parser.OFPMatch(eth_type=0x0806, arp_tpa=ip), [parser.OFPActionOutput(2)])

        elif dpid == 2:
            # Local delivery on s2
            for ip, port in [('10.0.0.2', 1), ('10.0.0.4', 4)]:
                self.add_flow(dp, 10, parser.OFPMatch(eth_type=0x0800, ipv4_dst=ip), [parser.OFPActionOutput(port)])
                self.add_flow(dp, 10, parser.OFPMatch(eth_type=0x0806, arp_tpa=ip), [parser.OFPActionOutput(port)])
            # Remote delivery to s1 (Default via Path A = port 2)
            for ip in ['10.0.0.1', '10.0.0.3']:
                self.add_flow(dp, 10, parser.OFPMatch(eth_type=0x0800, ipv4_dst=ip), [parser.OFPActionOutput(2)])
                self.add_flow(dp, 10, parser.OFPMatch(eth_type=0x0806, arp_tpa=ip), [parser.OFPActionOutput(2)])

    def _trigger_predictive_reroute(self, predicted_mbps, reason):
        """
        UNLISTED FEATURE: Preemptively splits traffic across Path B (Port 3)
        by installing a higher-priority (20) flow rule for h1 -> h2 traffic on s1,
        isolating h1's measurement stream from h3's background congestion on Path A.
        """
        if 1 not in self.datapaths or self.rerouted:
            return
        dp1 = self.datapaths[1]
        parser = dp1.ofproto_parser

        # Reroute h1 (10.0.0.1) -> h2 (10.0.0.2) onto Backup Path B (Port 3)
        match = parser.OFPMatch(eth_type=0x0800, ipv4_src='10.0.0.1', ipv4_dst='10.0.0.2')
        actions = [parser.OFPActionOutput(3)]
        self.add_flow(dp1, 20, match, actions)

        self.active_path = 3
        self.rerouted = True
        self.logger.warning(f"⚡ [PREDICTIVE REROUTE] {reason} (Predicted: {predicted_mbps:.2f} Mbps). "
                            f"Shifted Flow (10.0.0.1 -> 10.0.0.2) to Path B (Port 3)!")

    def _monitor_loop(self):
        while True:
            for dp in list(self.datapaths.values()):
                if dp.id == 1:  # Poll ingress switch s1
                    parser = dp.ofproto_parser
                    req = parser.OFPPortStatsRequest(dp, 0, dp.ofproto.OFPP_ANY)
                    dp.send_msg(req)
            hub.sleep(1.0)

    @set_ev_cls(ofp_event.EventOFPPortStatsReply, MAIN_DISPATCHER)
    def _port_stats_reply_handler(self, ev):
        dpid = ev.msg.datapath.id
        if dpid != 1:
            return

        now = time.time()
        port_mbps = {2: 0.0, 3: 0.0}

        for stat in ev.msg.body:
            port_no = stat.port_no
            if port_no in (2, 3):  # Inter-switch links Path A (2) and Path B (3)
                key = (dpid, port_no)
                total_bytes = stat.tx_bytes
                if key in self.prev_port_bytes:
                    prev_bytes, prev_ts = self.prev_port_bytes[key]
                    dt = max(0.001, now - prev_ts)
                    bw = ((total_bytes - prev_bytes) * 8) / (dt * 1e6)
                    port_mbps[port_no] = max(0.0, bw)
                self.prev_port_bytes[key] = (total_bytes, now)

        # Predictive Congestion Calculation on Primary Link (Port 2)
        curr_a = port_mbps[2]
        prev_a = self.prev_bw.get((dpid, 2), curr_a)
        derivative = curr_a - prev_a  # dB/dt (Mbps per second)
        predicted_a = max(0.0, curr_a + derivative)
        self.prev_bw[(dpid, 2)] = curr_a

        event_str = "NORMAL"
        if self.dynamic_enabled and not self.rerouted:
            if predicted_a >= self.predictive_threshold_mbps and curr_a >= 4.5:
                event_str = "PREEMPTIVE_REROUTE"
                self._trigger_predictive_reroute(predicted_a, "Derivative spike detected")
            elif curr_a >= 8.0:
                event_str = "REACTIVE_REROUTE"
                self._trigger_predictive_reroute(curr_a, "Threshold exceeded")
        elif self.rerouted:
            event_str = "LOAD_BALANCED_PATH_B"

        elapsed = round(now - self.start_time, 1)
        total_sdn = port_mbps[2] + port_mbps[3]

        self.writer.writerow([
            elapsed, dpid,
            round(port_mbps[2], 3), round(port_mbps[3], 3),
            round(total_sdn, 3), round(derivative, 3),
            round(predicted_a, 3), f"Path_{'B' if self.active_path == 3 else 'A'}",
            event_str
        ])
        self.csv_file.flush()
