#!/usr/bin/env python3
import csv
import json
import os
import subprocess
import sys
import threading
import time
from http.server import ThreadingHTTPServer, SimpleHTTPRequestHandler
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
    "packets_check", "bytes_check", "outage_s",
    "rate_pattern", "rate_min_pps", "rate_max_pps", "seed", "packet_reordering",
    "out_of_order_packets", "reordering_percent"
]

STATE = {
    "state": "idle",
    "run_id": 0,
    "start_ts": 0.0,
    "elapsed_s": 0.0,
    "phase": "idle",
    "error": None,
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
            # Older experiment CSVs do not contain the new traffic columns.
            for column in CSV_COLUMNS:
                row.setdefault(column, "")
            rid = int(row["run_id"])
            STATE["rows"].append(row)
            STATE["results_by_id"][rid] = {"row": row, "events": []}
            STATE["run_id"] = max(STATE["run_id"], rid)

def _cleanup_network():
    """Remove stale Mininet state and any leftover Ryu controller from a crashed run."""
    subprocess.run(["mn", "-c"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    subprocess.run(["pkill", "-f", "[r]un_ryu[.]py"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def execute_mininet_run(run_id, cfg):
    mode = cfg.get("mode", "dynamic")
    if mode not in ("static", "dynamic"):
        raise ValueError("mode must be 'static' or 'dynamic'")
    pattern = cfg.get("pattern", "fixed")
    if pattern not in ("fixed", "random"):
        raise ValueError("pattern must be 'fixed' or 'random'")
    rate = int(cfg.get("rate", 100))
    rate_pattern = cfg.get("rate_pattern", "constant")
    rate_min = int(cfg.get("rate_min", 300))
    rate_max = int(cfg.get("rate_max", 2000))
    if rate_pattern not in ("constant", "random", "burst"):
        raise ValueError("Unsupported rate pattern")
    if not (1 <= rate_min <= rate_max <= 5000):
        raise ValueError("Rate range must be within 1..5000 pps")
    reorder = cfg.get("packet_reordering", False)
    if not isinstance(reorder, bool):
        raise ValueError("packet_reordering must be a boolean")
    duration = int(cfg.get("duration", 15))
    loss_pct = float(cfg.get("loss_pct", 0.0))
    fail_at_s = cfg.get("fail_at_s", None)
    if fail_at_s in ("", None):
        fail_at_s = None
    else:
        fail_at_s = float(fail_at_s)
        if fail_at_s < 0 or fail_at_s >= duration:
            raise ValueError("fail_at_s must be within the run duration")
        if fail_at_s == int(fail_at_s):
            fail_at_s = int(fail_at_s)
    size = int(cfg.get("size", 1000))
    size_min = int(cfg.get("size_min", 200))
    size_max = int(cfg.get("size_max", 1472))
    seed = cfg.get("seed", None)
    seed = None if seed in ("", None) else int(seed)  # int(): also blocks argument injection
    if rate <= 0 or duration <= 0:
        raise ValueError("rate and duration must be positive")
    if not (12 <= size <= 1472 and 12 <= size_min <= size_max <= 1472):
        raise ValueError("packet size must be within 12..1472 bytes")
    if not (0.0 <= loss_pct <= 100.0):
        raise ValueError("loss_pct must be within 0..100")

    ryu_proc = None
    ryu_log = None
    net = None
    _cleanup_network()
    try:
        if os.path.exists(LIVE_FILE):
            os.remove(LIVE_FILE)

        env = os.environ.copy()
        env["SDN_MODE"] = mode
        env["SDN_LIVE_FILE"] = LIVE_FILE

        ryu_log = open(os.path.join(RESULTS_DIR, "controller.log"), "w")
        ryu_proc = subprocess.Popen(
            [os.environ.get("RYU_PYTHON", sys.executable), os.path.join(PROJECT_ROOT, "run_ryu.py"), os.path.join(PROJECT_ROOT, "network/controller.py")],
            cwd=PROJECT_ROOT, env=env, stdout=ryu_log, stderr=subprocess.STDOUT
        )
        time.sleep(2.5)
        if ryu_proc.poll() is not None:
            raise RuntimeError("Ryu controller exited at start-up; see results/controller.log")

        with LOCK:
            STATE["phase"] = "initializing_mininet"
        topo = TriangleSDNTopo(loss_pct=loss_pct)
        net = Mininet(
            topo=topo,
            controller=lambda name: RemoteController(name, ip="127.0.0.1", port=6633),
            switch=OVSKernelSwitch,
            link=TCLink,
            autoSetMacs=True
        )
        net.start()
        try:
            net.waitConnected(timeout=15)  # all switches must reach the controller
        except Exception:
            raise RuntimeError("Switches did not connect to the Ryu controller (port 6633)")
        time.sleep(1.0)  # let the controller finish installing its flow rules

        h1, h2 = net.get("h1", "h2")
        h1.cmd("arp -s 10.0.0.2 00:00:00:00:00:02")
        h2.cmd("arp -s 10.0.0.1 00:00:00:00:00:01")

        sender_json = os.path.join(RESULTS_DIR, "last_sender.json")
        receiver_json = os.path.join(RESULTS_DIR, "last_receiver.json")
        # Never reuse a previous run's output if this run's sender/receiver fails.
        for stale in (sender_json, receiver_json):
            if os.path.exists(stale):
                os.remove(stale)

        start_file = os.path.join(RESULTS_DIR, "measurement_start.json")
        if os.path.exists(start_file):
            os.remove(start_file)
        recv_proc = h2.popen([
            sys.executable, os.path.join(PROJECT_ROOT, "app/receiver.py"),
            "--port", "9999", "--duration", str(duration),
            "--measurement-start-file", start_file, "--out", receiver_json
        ])
        time.sleep(0.4)

        send_cmd = [
            "python3", os.path.join(PROJECT_ROOT, "app/sender.py"), "--ip", "10.0.0.2", "--port", "9999",
            "--rate", str(rate), "--rate-pattern", rate_pattern, "--rate-min", str(rate_min), "--rate-max", str(rate_max),
            "--duration", str(duration), "--pattern", pattern,
            "--size", str(size), "--size-min", str(size_min), "--size-max", str(size_max),
            "--out", sender_json,
        ]
        if seed is not None:
            send_cmd += ["--seed", str(seed)]
        if reorder:
            send_cmd.append("--reorder")

        measurement_start = time.time()
        with open(start_file, "w") as f:
            json.dump({"start_ts": measurement_start, "duration_s": duration}, f)
        with LOCK:
            STATE["start_ts"] = measurement_start
            STATE["phase"] = "measuring"

        send_proc = h1.popen(send_cmd)

        link_down_triggered = False
        link_up_triggered = False
        up_at_s = (fail_at_s + 6) if fail_at_s is not None else None

        while send_proc.poll() is None:
            el = time.time() - measurement_start
            with LOCK:
                STATE["elapsed_s"] = round(el, 1)

            if fail_at_s is not None and not link_down_triggered and el >= fail_at_s:
                link_down_triggered = True
                net.configLinkStatus("s1", "s3", "down")

            if up_at_s is not None and not link_up_triggered and el >= up_at_s:
                link_up_triggered = True
                net.configLinkStatus("s1", "s3", "up")

            time.sleep(0.2)

        if send_proc.returncode != 0:
            raise RuntimeError(f"UDP sender failed (exit code {send_proc.returncode})")
        recv_proc.wait(timeout=duration + 10)
        time.sleep(1.0)  # Let final OpenFlow stats poll complete

        live_data = {"packets": 0, "bytes": 0, "rate": 0, "events": []}
        if os.path.exists(LIVE_FILE):
            with open(LIVE_FILE) as f:
                live_data = json.load(f)

        tx_data = json.load(open(sender_json)) if os.path.exists(sender_json) else {}
        rx_data = json.load(open(receiver_json)) if os.path.exists(receiver_json) else {}
    finally:
        # Always tear down, even on error, so port 6633 and Mininet state are released.
        if net is not None:
            try:
                net.stop()
            except Exception:
                pass
        if ryu_proc is not None:
            ryu_proc.terminate()
            try:
                ryu_proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                ryu_proc.kill()
        if ryu_log is not None:
            ryu_log.close()

    app_sent = tx_data.get("app_sent", 0)
    app_recv = rx_data.get("app_received", 0)
    sw_pkts = live_data.get("packets", 0)
    sw_bytes = live_data.get("bytes", 0)

    exp_pkts = tx_data.get("expected_switch_packets", app_sent + 5)
    exp_bytes = tx_data.get("expected_switch_bytes", 0)

    loss_calc = round(max(0.0, 100.0 - (app_recv / max(1, app_sent) * 100.0)), 2)
    # Sender count is authoritative even if the receiver never gets an EOF marker.
    if app_sent and rx_data:
        rx_data["expected_packets"] = app_sent
        rx_data["packets_lost"] = max(0, app_sent - app_recv)
        rx_data["loss_percent"] = loss_calc
    outage_val = "" if fail_at_s is None else rx_data.get("outage_s", "")

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
        "outage_s": outage_val,
        "rate_pattern": rate_pattern,
        "rate_min_pps": rate_min if rate_pattern != "constant" else "",
        "rate_max_pps": rate_max if rate_pattern != "constant" else "",
        "seed": "" if seed is None else seed,
        "packet_reordering": "enabled" if reorder else "disabled",
        "out_of_order_packets": rx_data.get("out_of_order_packets", 0),
        "reordering_percent": rx_data.get("reordering_percent", 0.0)
    }

    # Show events relative to the actual traffic measurement window.
    relative_events = []
    for event in live_data.get("events", []):
        event = dict(event)
        if isinstance(event.get("t"), (int, float)):
            event["rt"] = round(event["t"] - measurement_start, 2)
        relative_events.append(event)

    # Migrate the old CSV header before appending; keep all historical rows.
    if os.path.exists(CSV_PATH):
        with open(CSV_PATH, "r", newline="") as f:
            old_reader = csv.DictReader(f)
            old_columns = old_reader.fieldnames or []
            if old_columns != CSV_COLUMNS:
                old_rows = list(old_reader)
            else:
                old_rows = None
        if old_rows is not None:
            temp_csv = CSV_PATH + ".tmp"
            with open(temp_csv, "w", newline="") as f:
                writer = csv.DictWriter(f, fieldnames=CSV_COLUMNS)
                writer.writeheader()
                for old_row in old_rows:
                    writer.writerow({k: old_row.get(k, "") for k in CSV_COLUMNS})
            os.replace(temp_csv, CSV_PATH)
    with open(CSV_PATH, "a", newline="") as f:
        csv.DictWriter(f, fieldnames=CSV_COLUMNS).writerow(row)

    with LOCK:
        STATE["rows"].append(row)
        STATE["results_by_id"][run_id] = {"row": row, "events": relative_events}
        STATE["elapsed_s"] = duration
        STATE["state"] = "done"
        STATE["phase"] = "done"

def safe_execute_mininet_run(run_id, cfg):
    try:
        execute_mininet_run(run_id, cfg)
    except Exception as exc:
        import traceback
        traceback.print_exc()
        with LOCK:
            STATE["state"] = "error"
            STATE["phase"] = "error"
            STATE["error"] = str(exc)
        # A failed Mininet run may leave virtual interfaces behind.
        _cleanup_network()


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
                    "elapsed_s": STATE["elapsed_s"],
                    "start_ts": STATE["start_ts"],
                    "phase": STATE["phase"],
                    "error": STATE["error"]
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
            try:
                rid = int(parsed.path.split("/")[-1])
            except ValueError:
                self._send_json({"error": "invalid run id"}, 400)
                return
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
            try:
                length = int(self.headers.get("Content-Length", 0))
                cfg = json.loads(self.rfile.read(length).decode("utf-8"))
                if not isinstance(cfg, dict):
                    raise ValueError("body must be a JSON object")
            except (ValueError, UnicodeDecodeError) as exc:
                self._send_json({"error": f"invalid request: {exc}"}, 400)
                return
            with LOCK:
                if STATE["state"] == "running":
                    self._send_json({"error": "Experiment already running"}, 409)
                    return
                STATE["run_id"] += 1
                run_id = STATE["run_id"]
                STATE["state"] = "running"
                STATE["elapsed_s"] = 0.0
                STATE["start_ts"] = 0.0
                STATE["phase"] = "preparing"
                STATE["error"] = None

            t = threading.Thread(target=safe_execute_mininet_run, args=(run_id, cfg), daemon=True)
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
    ThreadingHTTPServer(("127.0.0.1", port), DashboardHandler).serve_forever()
