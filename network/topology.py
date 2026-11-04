#!/usr/bin/env python3
from mininet.topo import Topo
from mininet.node import OVSKernelSwitch
from mininet.link import TCLink

class TriangleSDNTopo(Topo):
    """
    Matches index.html SVG topology exactly:
      - h1 (10.0.0.1) connected to s1 port 1
      - Primary Path: s1 (port 2) <---> s3 (port 2)
      - Backup Path:  s1 (port 3) <---> s2 (port 1) AND s2 (port 2) <---> s3 (port 3)
      - h2 (10.0.0.2) connected to s3 port 1 (with configurable % packet loss)
    """
    def build(self, loss_pct=0.0):
        s1 = self.addSwitch("s1", cls=OVSKernelSwitch, protocols="OpenFlow13")
        s2 = self.addSwitch("s2", cls=OVSKernelSwitch, protocols="OpenFlow13")
        s3 = self.addSwitch("s3", cls=OVSKernelSwitch, protocols="OpenFlow13")

        h1 = self.addHost("h1", ip="10.0.0.1/24", mac="00:00:00:00:00:01")
        h2 = self.addHost("h2", ip="10.0.0.2/24", mac="00:00:00:00:00:02")

        self.addLink(h1, s1, port1=1, port2=1, cls=TCLink, bw=100)
        if loss_pct > 0:
            self.addLink(h2, s3, port1=1, port2=1, cls=TCLink, bw=100, loss=loss_pct)
        else:
            self.addLink(h2, s3, port1=1, port2=1, cls=TCLink, bw=100)

        # Primary Path (s1 port 2 <-> s3 port 2)
        self.addLink(s1, s3, port1=2, port2=2, cls=TCLink, bw=100)
        # Backup Path via s2 (s1 port 3 <-> s2 port 1, s2 port 2 <-> s3 port 3)
        self.addLink(s1, s2, port1=3, port2=1, cls=TCLink, bw=100)
        self.addLink(s2, s3, port1=2, port2=3, cls=TCLink, bw=100)
