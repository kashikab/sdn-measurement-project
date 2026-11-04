#!/usr/bin/env python3
from mininet.topo import Topo
from mininet.net import Mininet
from mininet.node import RemoteController, OVSKernelSwitch
from mininet.link import TCLink
from mininet.cli import CLI
from mininet.log import setLogLevel, info

class MultipathSDNTopo(Topo):
    """
    Multipath Topology:
       h1 (10.0.0.1) ---\              /--- h2 (10.0.0.2 - Receiver)
                         [s1] ====== [s2]
       h3 (10.0.0.3) ---/   Path A/B   \--- h4 (10.0.0.4)

    Switch Port Mapping:
      s1-eth1: h1       | s2-eth1: h2
      s1-eth4: h3       | s2-eth4: h4
      s1-eth2 <-> s2-eth2 : Primary Link (Path A - 10 Mbps)
      s1-eth3 <-> s2-eth3 : Backup Link  (Path B - 10 Mbps)
    """
    def build(self):
        s1 = self.addSwitch('s1', cls=OVSKernelSwitch, protocols='OpenFlow13')
        s2 = self.addSwitch('s2', cls=OVSKernelSwitch, protocols='OpenFlow13')

        h1 = self.addHost('h1', ip='10.0.0.1/24', mac='00:00:00:00:00:01')
        h2 = self.addHost('h2', ip='10.0.0.2/24', mac='00:00:00:00:00:02')
        h3 = self.addHost('h3', ip='10.0.0.3/24', mac='00:00:00:00:00:03')
        h4 = self.addHost('h4', ip='10.0.0.4/24', mac='00:00:00:00:00:04')

        # Host Access Links (50 Mbps)
        self.addLink(h1, s1, port1=1, port2=1, cls=TCLink, bw=50)
        self.addLink(h3, s1, port1=1, port2=4, cls=TCLink, bw=50)
        self.addLink(h2, s2, port1=1, port2=1, cls=TCLink, bw=50)
        self.addLink(h4, s2, port1=1, port2=4, cls=TCLink, bw=50)

        # Inter-Switch Bottleneck Links (10 Mbps each to demonstrate congestion & rerouting)
        self.addLink(s1, s2, port1=2, port2=2, cls=TCLink, bw=10, delay='2ms', max_queue_size=100) # Path A
        self.addLink(s1, s2, port1=3, port2=3, cls=TCLink, bw=10, delay='2ms', max_queue_size=100) # Path B

def start_network():
    topo = MultipathSDNTopo()
    net = Mininet(
        topo=topo,
        controller=lambda name: RemoteController(name, ip='127.0.0.1', port=6633),
        switch=OVSKernelSwitch,
        link=TCLink,
        autoSetMacs=True
    )
    net.start()
    info("*** Multipath SDN Network Started (Path A: port 2, Path B: port 3)\n")
    CLI(net)
    net.stop()

if __name__ == '__main__':
    setLogLevel('info')
    start_network()
