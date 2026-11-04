#!/usr/bin/env python3
"""
Automated End-to-End Experiment Runner:
Runs two full experiments back-to-back in Mininet:
  1. Static Baseline (SDN_DYNAMIC=0): Primary link saturates when h3 injects burst traffic -> heavy packet loss.
  2. Dynamic Predictive SDN (SDN_DYNAMIC=1): Controller detects dB/dt derivative spike and preemptively
     reroutes h1 -> h2 onto Path B (Port 3) -> near-zero packet loss and 2x aggregate throughput.
"""
import os
import time
import subprocess
from mininet.net import Mininet
from mininet.node import RemoteController, OVSKernelSwitch
from mininet.link import TCLink
from mininet.log import setLogLevel, info
import sys

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
from network.topology import MultipathSDNTopo

def run_experiment(mode_name, dynamic_flag):
    info(f"\n======================================================\n")
    info(f"🚀 STARTING EXPERIMENT: {mode_name.upper()} (SDN_DYNAMIC={dynamic_flag})\n")
    info(f"======================================================\n")

    os.makedirs('results', exist_ok=True)
    subprocess.run("pkill -9 -f ryu-manager", shell=True, stderr=subprocess.DEVNULL)
    subprocess.run("mn -c", shell=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    time.sleep(1)

    env = os.environ.copy()
    env['SDN_DYNAMIC'] = str(dynamic_flag)
    env['SDN_CSV_PATH'] = f"results/sdn_{mode_name}.csv"

    ryu_proc = subprocess.Popen(
        ["ryu-manager", "network/controller.py"],
        env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT
    )
    time.sleep(3)

    topo = MultipathSDNTopo()
    net = Mininet(
        topo=topo,
        controller=lambda name: RemoteController(name, ip='127.0.0.1', port=6633),
        switch=OVSKernelSwitch,
        link=TCLink,
        autoSetMacs=True
    )
    net.start()
    time.sleep(2)

    h1, h2, h3 = net.get('h1', 'h2', 'h3')

    info(f"*** [{mode_name}] Starting Receiver on h2 (10.0.0.2)...\n")
    recv_proc = h2.popen(
        f"python3 app/receiver.py --port 9999 --timeout 5 --csv results/receiver_{mode_name}.csv"
    )
    time.sleep(1)

    info(f"*** [{mode_name}] Starting Randomized Primary Flow on h1 -> h2 (6 Mbps avg, 18s)...\n")
    send1_proc = h1.popen(
        f"python3 app/sender.py --ip 10.0.0.2 --port 9999 --duration 18 "
        f"--mode random --rate 6.0 --csv results/sender_h1_{mode_name}.csv --id h1_primary"
    )

    # Wait 5 seconds, then trigger CHALLENGING SCENARIO (h3 congestion burst)
    time.sleep(5)
    info(f"*** ⚡ [{mode_name}] CHALLENGING SCENARIO: Injecting 7 Mbps Ramp Burst from h3 -> h2!\n")
    send3_proc = h3.popen(
        f"python3 app/sender.py --ip 10.0.0.2 --port 9999 --duration 10 "
        f"--mode ramp --rate 7.5 --csv results/sender_h3_{mode_name}.csv --id h3_attacker"
    )

    send1_proc.wait()
    send3_proc.wait()
    recv_proc.wait()

    info(f"*** [{mode_name}] Experiment Complete. Logs saved to results/*_{mode_name}.csv\n")
    net.stop()
    ryu_proc.terminate()
    time.sleep(1)

if __name__ == '__main__':
    setLogLevel('info')
    if os.geteuid() != 0:
        print("ERROR: Mininet requires root privileges. Run with: sudo python3 integration/run_demo.py")
        sys.exit(1)

    run_experiment("static_baseline", dynamic_flag=0)
    run_experiment("dynamic_predictive", dynamic_flag=1)
    print("\n✅ Both Baseline and Dynamic Predictive experiments finished!")
    print("👉 Launch the UI now with: streamlit run ui/dashboard.py")
