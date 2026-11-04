#!/usr/bin/env python3
import csv
import json
import os
import subprocess
import sys
import threading
import time
from http.server import HTTPServer, SimpleHTTPRequestHandler
from urllib.parse import urlparse

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, PROJECT_ROOT)

from mininet.net import Mininet
from mininet.node import RemoteController, OVSKernelSwitch
from mininet.link import TCLink
from network.topology import TriangleSDNTopo

RESULTS_DIR = os.path.join(PROJECT_ROOT, "results")
CSV_PATH = os.path.join(RESULTS_DIR, "experiments.csv")
LIVE_FILE = os.path.join(RESULTS_DIR, "live_telemetry.json")

CSV_COLUMNS = [
    "run_id", "group", "mode", "rate_pps", "size_bytes", "duration_s",
    "loss_pct", "fail_at_s", "app_sent", "app_received", "app_loss_pct",
    "app_throughput_bps", "switch_packets", "switch_bytes",
    "packets_check", "bytes_check", "outage_s"
]

STATE = {
    "state": "idle",
    "run_id": 0,
    "start_ts": 0.0,
    "elapsed_s": 0.0,
    "results_by_id": {},
    "rows": []
}
LOCK = threading.Lock()

def load_existing_csv():
    os.makedirs(RESULTS_DIR, exist_ok=True)
    if not os.path.exists(CSV_PATH):
        with open(CSV_PATH, "w", newline="") as f:
            csv.DictWriter(f, fieldnames=CSV_COLUMNS).writeheader()
        return
    with open(CSV_PATH, "r", newline="") as f:
        for row in csv.DictReader(f):
            rid = int(row["run_id"])
            STATE["rows"].append(row)
            STATE["results_by_id"][rid] = {"row": row, "events": []}
            STATE["run_id"] = max(STATE["run_id"], rid)

def execute_mininet_run(run_id, cfg):
    mode = cfg.get("mode", "dynamic")
    pattern = cfg.get("pattern", "fixed")
    rate = int(cfg.get("rate", 100))
    duration = int(cfg.get("duration", 15))
    loss_pct = float(cfg.get("loss_pct", 0.0))
    fail_at_s = cfg.get("fail_at_s", None)
    size = int(cfg.get("size", 1000))
    size_min = int(cfg.get("size_min", 200))
    size_max = int(cfg.get("size_max", 1472))
    seed = cfg.get("seed", None)

    subprocess.run("pkill -9 -f ryu-manager", shell=True, stderr=subprocess.DEVNULL)
    subprocess.run("mn -c", shell=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    if os.path.exists(LIVE_FILE):
        os.remove(LIVE_FILE)

    env = os.environ.copy()
    env["SDN_MODE"] = mode
    env["SDN_LIVE_FILE"] = LIVE_FILE

    ryu_proc = subprocess.Popen(
        ["ryu-manager", os.path.join(PROJECT_ROOT, "network/controller.py")],
        env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
    )
    time.sleep(2.5)

    topo = TriangleSDNTopo(loss_pct=loss_pct)
    net = Mininet(
        topo=topo,
        controller=lambda name: RemoteController(name, ip="127.0.0.1", port=6633),
        switch=OVSKernelSwitch,
        link=TCLink,
        autoSetMacs=True
    )
    net.start()
    time.sleep(1.5)

    h1, h2 = net.get("h1", "h2")
    h1.cmd("arp -s 10.0.0.2 00:00:00:00:00:02")
    h2.cmd("arp -s 10.0.0.1 00:00:00:00:00:01")

    sender_json = os.path.join(RESULTS_DIR, "last_sender.json")
    receiver_json = os.path.join(RESULTS_DIR, "last_receiver.json")

    recv_proc = h2.popen(
        f"python3 {PROJECT_ROOT}/app/receiver.py --port 9999 --duration {duration} --out {receiver_json}"
    )
    time.sleep(0.4)

    seed_arg = f"--seed {seed}" if seed is not None else ""
    send_cmd = (
        f"python3 {PROJECT_ROOT}/app/sender.py --ip 10.0.0.2 --port 9999 "
        f"--rate {rate} --duration {duration} --pattern {pattern} "
        f"--size {size} --size-min {size_min} --size-max {size_max} {seed_arg} --out {sender_json}"
    )

    with LOCK:
        STATE["start_ts"] = time.time()

    send_proc = h1.popen(send_cmd)

    link_down_triggered = False
    link_up_triggered = False
    up_at_s = (fail_at_s + 6) if fail_at_s is not None else None

    while send_proc.poll() is None:
        el = time.time() - STATE["start_ts"]
        with LOCK:
            STATE["elapsed_s"] = round(el, 1)

        if fail_at_s is not None and not link_down_triggered and el >= fail_at_s:
            link_down_triggered = True
            net.configLinkStatus("s1", "s3", "down")

        if up_at_s is not None and not link_up_triggered and el >= up_at_s:
            link_up_triggered = True
            net.configLinkStatus("s1", "s3", "up")

        time.sleep(0.2)

    recv_proc.wait()
    time.sleep(1.0)  # Let final OpenFlow stats poll complete

    live_data = {"packets": 0, "bytes": 0, "rate": 0, "events": []}
    if os.path.exists(LIVE_FILE):
        with open(LIVE_FILE) as f:
            live_data = json.load(f)

    tx_data = json.load(open(sender_json)) if os.path.exists(sender_json) else {}
    rx_data = json.load(open(receiver_json)) if os.path.exists(receiver_json) else {}

    net.stop()
    ryu_proc.terminate()

    app_sent = tx_data.get("app_sent", 0)
    app_recv = rx_data.get("app_received", 0)
    sw_pkts = live_data.get("packets", 0)
    sw_bytes = live_data.get("bytes", 0)

    exp_pkts = tx_data.get("expected_switch_packets", app_sent + 5)
    exp_bytes = tx_data.get("expected_switch_bytes", 0)

    loss_calc = round(max(0.0, 100.0 - (app_recv / max(1, app_sent) * 100.0)), 2)
    outage_val = "" if fail_at_s is None else rx_data.get("outage_s", 0.0)

    row = {
        "run_id": run_id,
        "group": "D" if fail_at_s is not None else ("B" if pattern == "random" else "A"),
        "mode": mode,
        "rate_pps": rate,
        "size_bytes": "" if pattern == "random" else size,
        "duration_s": duration,
        "loss_pct": loss_pct,
        "fail_at_s": "" if fail_at_s is None else fail_at_s,
        "app_sent": app_sent,
        "app_received": app_recv,
        "app_loss_pct": loss_calc,
        "app_throughput_bps": rx_data.get("app_throughput_bps", 0),
        "switch_packets": sw_pkts,
        "switch_bytes": sw_bytes,
        "packets_check": "PASS" if sw_pkts == exp_pkts else "FAIL",
        "bytes_check": "PASS" if sw_bytes == exp_bytes else "FAIL",
        "outage_s": outage_val
    }

    with open(CSV_PATH, "a", newline="") as f:
        csv.DictWriter(f, fieldnames=CSV_COLUMNS).writerow(row)

    with LOCK:
        STATE["rows"].append(row)
        STATE["results_by_id"][run_id] = {"row": row, "events": live_data.get("events", [])}
        STATE["elapsed_s"] = duration
        STATE["state"] = "done"

class DashboardHandler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=os.path.join(PROJECT_ROOT, "web"), **kwargs)

    def _send_json(self, payload, status=200):
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path == "/api/status":
            with LOCK:
                self._send_json({
                    "state": STATE["state"],
                    "run_id": STATE["run_id"],
                    "elapsed_s": STATE["elapsed_s"]
                })
        elif parsed.path == "/api/live":
            if os.path.exists(LIVE_FILE):
                try:
                    with open(LIVE_FILE) as f:
                        self._send_json(json.load(f))
                    return
                except Exception:
                    pass
            self._send_json({"packets": 0, "bytes": 0, "rate": 0, "events": []})
        elif parsed.path.startswith("/api/result/"):
            rid = int(parsed.path.split("/")[-1])
            with LOCK:
                res = STATE["results_by_id"].get(rid, {})
            self._send_json(res)
        elif parsed.path == "/api/results":
            with LOCK:
                self._send_json({"rows": STATE["rows"]})
        else:
            super().do_GET()

    def do_POST(self):
        if self.path == "/api/run":
            length = int(self.headers.get("Content-Length", 0))
            cfg = json.loads(self.rfile.read(length).decode("utf-8"))
            with LOCK:
                if STATE["state"] == "running":
                    self._send_json({"error": "Experiment already running"}, 409)
                    return
                STATE["run_id"] += 1
                run_id = STATE["run_id"]
                STATE["state"] = "running"
                STATE["elapsed_s"] = 0.0

            t = threading.Thread(target=execute_mininet_run, args=(run_id, cfg), daemon=True)
            t.start()
            self._send_json({"run_id": run_id})
        else:
            self.send_error(404)

if __name__ == "__main__":
    if os.geteuid() != 0:
        print("ERROR: Run with sudo so Mininet can create virtual switches: sudo python3 web/server.py")
        sys.exit(1)
    load_existing_csv()
    port = 8000
    print(f"🚀 SDN Web UI Server running at http://127.0.0.1:{port}")
    HTTPServer(("0.0.0.0", port), DashboardHandler).serve_forever()
