import argparse

from mininet.cli import CLI
from mininet.link import TCLink
from mininet.log import setLogLevel
from mininet.net import Mininet
from mininet.node import OVSSwitch, RemoteController


def build(ip, port):
    net = Mininet(controller=None, switch=OVSSwitch, link=TCLink,
                  autoSetMacs=False, build=False)

    net.addController('c0', controller=RemoteController, ip=ip, port=port)
    s1 = net.addSwitch('s1', protocols='OpenFlow13')

    h1 = net.addHost('h1', ip='10.0.0.1/24', mac='00:00:00:00:00:01')
    h2 = net.addHost('h2', ip='10.0.0.2/24', mac='00:00:00:00:00:02')

    net.addLink(h1, s1)
    net.addLink(h2, s1)

    net.build()
    return net


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--ip', default='127.0.0.1', help='controller IP')
    parser.add_argument('--port', type=int, default=6653,
                        help='controller OpenFlow port')
    args = parser.parse_args()

    setLogLevel('info')
    net = build(args.ip, args.port)
    net.start()
    print('\n*** h1 = 10.0.0.1 (sender), h2 = 10.0.0.2 (receiver)')
    print('*** Try: h1 ping -c 3 h2\n')
    CLI(net)
    net.stop()
