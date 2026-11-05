#!/usr/bin/env python3
"""Run one reproducible UDP measurement experiment inside the supplied Mininet topology.

Run: sudo env "PATH=$PWD/.venv/bin:$PATH" .venv/bin/python integration/run_demo.py --mode dynamic
"""
import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path

from mininet.net import Mininet
from mininet.node import RemoteController, OVSKernelSwitch
from mininet.link import TCLink
from mininet.log import setLogLevel, info

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from network.topology import TriangleSDNTopo


def main():
    parser = argparse.ArgumentParser(description="Run a UDP bandwidth measurement in Mininet")
    parser.add_argument("--mode", choices=["static", "dynamic"], default="dynamic")
    parser.add_argument("--rate", type=float, default=500)
    parser.add_argument("--rate-pattern", choices=["constant", "random", "burst"], default="constant")
    parser.add_argument("--rate-min", type=int, default=300)
    parser.add_argument("--rate-max", type=int, default=2000)
    parser.add_argument("--duration", type=float, default=10)
    parser.add_argument("--pattern", choices=["fixed", "random"], default="fixed")
    parser.add_argument("--size", type=int, default=1000)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--reorder", action="store_true", help="Enable sender-side packet reordering")
    args = parser.parse_args()
    if not (1 <= args.rate_min <= args.rate_max <= 5000):
        parser.error("rate range must satisfy 1 <= min <= max <= 5000")
    if os.geteuid() != 0:
        parser.error("Mininet requires root; run with sudo")

    results = ROOT / "results"
    results.mkdir(exist_ok=True)
    env = os.environ.copy()
    env["SDN_MODE"] = args.mode
    env["SDN_LIVE_FILE"] = str(results / "live_telemetry.json")
    sender_out = results / "last_sender.json"
    receiver_out = results / "last_receiver.json"

    # Remove stale outputs so a failed run can never report a previous run's numbers.
    for stale in (sender_out, receiver_out, results / "live_telemetry.json"):
        if stale.exists():
            stale.unlink()
    log = open(results / "controller.log", "w")
    controller = subprocess.Popen([os.environ.get("RYU_PYTHON", sys.executable), str(ROOT / "run_ryu.py"), str(ROOT / "network/controller.py")], cwd=str(ROOT), env=env, stdout=log, stderr=subprocess.STDOUT)
    time.sleep(2.5)
    net = None
    try:
        if controller.poll() is not None:
            raise RuntimeError("Ryu exited at start-up; see results/controller.log")
        net = Mininet(topo=TriangleSDNTopo(), controller=lambda name: RemoteController(name, ip="127.0.0.1", port=6633), switch=OVSKernelSwitch, link=TCLink, autoSetMacs=True)
        net.start()
        net.waitConnected(timeout=15)
        time.sleep(1.0)  # let the controller install its flow rules
        h1, h2 = net.get("h1", "h2")
        info("*** Starting UDP receiver on h2\n")
        receiver = h2.popen(["python3", str(ROOT / "app/receiver.py"), "--port", "9999", "--duration", str(args.duration + 3), "--out", str(receiver_out)])
        time.sleep(0.5)
        cmd = ["python3", str(ROOT / "app/sender.py"), "--ip", "10.0.0.2", "--port", "9999", "--rate", str(args.rate), "--rate-pattern", args.rate_pattern, "--rate-min", str(args.rate_min), "--rate-max", str(args.rate_max), "--duration", str(args.duration), "--pattern", args.pattern, "--size", str(args.size), "--seed", str(args.seed), "--out", str(sender_out)]
        if args.reorder:
            cmd.append("--reorder")
        info("*** Sending UDP measurement traffic from h1 to h2\n")
        sender = h1.popen(cmd)
        if sender.wait() != 0:
            raise RuntimeError("UDP sender failed")
        receiver.wait(timeout=args.duration + 8)
        time.sleep(1.5)  # allow the controller's final flow-stat poll
        tx = json.loads(sender_out.read_text())
        rx = json.loads(receiver_out.read_text())
        telemetry_path = results / "live_telemetry.json"
        telemetry = json.loads(telemetry_path.read_text()) if telemetry_path.exists() else {}
        summary = {"mode": args.mode, "sender": tx, "receiver": rx, "switch": telemetry}
        (results / "last_experiment.json").write_text(json.dumps(summary, indent=2))
        print(json.dumps(summary, indent=2))
    finally:
        if net is not None:
            net.stop()
        log.close()
        controller.terminate()
        try:
            controller.wait(timeout=3)
        except subprocess.TimeoutExpired:
            controller.kill()


if __name__ == "__main__":
    setLogLevel("info")
    main()
