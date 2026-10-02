import csv
import os
import time

from ryu.base import app_manager
from ryu.controller import ofp_event
from ryu.controller.handler import (CONFIG_DISPATCHER, DEAD_DISPATCHER,
                                    MAIN_DISPATCHER, set_ev_cls)
from ryu.lib import hub
from ryu.lib.packet import ethernet, packet
from ryu.ofproto import ofproto_v1_3

H1_IP, H2_IP = '10.0.0.1', '10.0.0.2'
H1_PORT, H2_PORT = 1, 2
UDP_PORT = 5001
POLL_INTERVAL = 2
MEASURE_PRIORITY = 100
MEASURE_COOKIE = 0x5001
CSV_PATH = os.environ.get(
    'FLOW_STATS_CSV',
    os.path.join(os.path.dirname(os.path.abspath(__file__)),
                 'results', 'flow_stats.csv'))
CSV_HEADER = ['timestamp', 'flow', 'packet_count', 'byte_count',
              'duration_sec']
FLOW_NAME = '%s->%s:udp/%d' % (H1_IP, H2_IP, UDP_PORT)


class BandwidthMeterController(app_manager.RyuApp):
    OFP_VERSIONS = [ofproto_v1_3.OFP_VERSION]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.mac_to_port = {}
        self.datapaths = {}

        os.makedirs(os.path.dirname(CSV_PATH), exist_ok=True)
        with open(CSV_PATH, 'w', newline='') as f:
            csv.writer(f).writerow(CSV_HEADER)
        self.logger.info('Writing flow stats to %s', CSV_PATH)

        self.monitor_thread = hub.spawn(self._monitor)

    def add_flow(self, dp, priority, match, actions, cookie=0,
                 idle_timeout=0, buffer_id=None):
        ofp, parser = dp.ofproto, dp.ofproto_parser
        inst = [parser.OFPInstructionActions(ofp.OFPIT_APPLY_ACTIONS,
                                             actions)]
        kwargs = dict(datapath=dp, priority=priority, match=match,
                      instructions=inst, cookie=cookie,
                      idle_timeout=idle_timeout)
        if buffer_id is not None and buffer_id != ofp.OFP_NO_BUFFER:
            kwargs['buffer_id'] = buffer_id
        dp.send_msg(parser.OFPFlowMod(**kwargs))

    @set_ev_cls(ofp_event.EventOFPSwitchFeatures, CONFIG_DISPATCHER)
    def switch_features_handler(self, ev):
        dp = ev.msg.datapath
        ofp, parser = dp.ofproto, dp.ofproto_parser

        match = parser.OFPMatch()
        actions = [parser.OFPActionOutput(ofp.OFPP_CONTROLLER,
                                          ofp.OFPCML_NO_BUFFER)]
        self.add_flow(dp, 0, match, actions)

        match = parser.OFPMatch(eth_type=0x0800, ip_proto=17,
                                ipv4_src=H1_IP, ipv4_dst=H2_IP,
                                udp_dst=UDP_PORT)
        actions = [parser.OFPActionOutput(H2_PORT)]
        self.add_flow(dp, MEASURE_PRIORITY, match, actions,
                      cookie=MEASURE_COOKIE)
        self.logger.info('s%s: installed table-miss and measurement rule '
                         '(%s)', dp.id, FLOW_NAME)

    @set_ev_cls(ofp_event.EventOFPPacketIn, MAIN_DISPATCHER)
    def packet_in_handler(self, ev):
        msg = ev.msg
        dp = msg.datapath
        ofp, parser = dp.ofproto, dp.ofproto_parser
        in_port = msg.match['in_port']

        pkt = packet.Packet(msg.data)
        eth = pkt.get_protocols(ethernet.ethernet)[0]
        if eth.ethertype == 0x88cc:
            return

        src, dst = eth.src, eth.dst
        table = self.mac_to_port.setdefault(dp.id, {})
        table[src] = in_port

        out_port = table.get(dst, ofp.OFPP_FLOOD)
        actions = [parser.OFPActionOutput(out_port)]

        if out_port != ofp.OFPP_FLOOD:
            match = parser.OFPMatch(in_port=in_port, eth_dst=dst,
                                    eth_src=src)
            self.add_flow(dp, 1, match, actions, idle_timeout=30,
                          buffer_id=msg.buffer_id)
            if msg.buffer_id != ofp.OFP_NO_BUFFER:
                return

        data = None
        if msg.buffer_id == ofp.OFP_NO_BUFFER:
            data = msg.data
        dp.send_msg(parser.OFPPacketOut(datapath=dp,
                                        buffer_id=msg.buffer_id,
                                        in_port=in_port, actions=actions,
                                        data=data))

    @set_ev_cls(ofp_event.EventOFPStateChange,
                [MAIN_DISPATCHER, DEAD_DISPATCHER])
    def state_change_handler(self, ev):
        dp = ev.datapath
        if ev.state == MAIN_DISPATCHER:
            self.datapaths[dp.id] = dp
        elif ev.state == DEAD_DISPATCHER:
            self.datapaths.pop(dp.id, None)

    def _monitor(self):
        while True:
            for dp in list(self.datapaths.values()):
                parser = dp.ofproto_parser
                dp.send_msg(parser.OFPFlowStatsRequest(dp))
            hub.sleep(POLL_INTERVAL)

    @set_ev_cls(ofp_event.EventOFPFlowStatsReply, MAIN_DISPATCHER)
    def flow_stats_reply_handler(self, ev):
        now = time.time()
        for stat in ev.msg.body:
            if stat.cookie != MEASURE_COOKIE:
                continue
            row = [round(now, 3), FLOW_NAME, stat.packet_count,
                   stat.byte_count,
                   round(stat.duration_sec + stat.duration_nsec / 1e9, 3)]
            with open(CSV_PATH, 'a', newline='') as f:
                csv.writer(f).writerow(row)
            self.logger.info('[stats] %s pkts=%d bytes=%d', FLOW_NAME,
                             stat.packet_count, stat.byte_count)
