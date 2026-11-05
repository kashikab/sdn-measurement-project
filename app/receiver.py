#!/usr/bin/env python3
"""UDP receiver with one worker thread per sender.

The main thread only receives datagrams and timestamps them. Each distinct
sender (source IP and source port, i.e. one sender process) gets its own
worker thread and queue, which does the counting, loss detection and
end-marker handling for that sender. Several senders can therefore be measured
at the same time without mixing their sequence numbers.

The top-level fields of the output JSON are totals over all senders (this is
what web/server.py reads, so a single-sender run looks exactly as before).
The per-sender figures are under the "senders" key.
"""
import argparse
import json
import os
import queue
import socket
import struct
import threading
import time

HEADER_FORMAT = "!Id"
HEADER_SIZE = struct.calcsize(HEADER_FORMAT)
EOF_SEQ = 0xFFFFFFFF
EOF_GRACE_S = 0.5      # keep draining briefly after a sender's first end marker
OUTAGE_GAP_S = 0.25    # inter-arrival gap that counts as an outage


class SenderWorker(threading.Thread):
    """Counts the packets of ONE sender."""

    def __init__(self, label):
        super().__init__(daemon=True)
        self.label = label
        self.q = queue.Queue()
        self.rx_packets = 0
        self.rx_bytes = 0
        self.seen = set()
        self.out_of_order = 0
        self.highest = -1
        self.expected = None
        self.malformed = 0
        self.first_ts = None
        self.last_ts = None
        self.outage_s = 0.0
        self.prev_ts = None
        self.eof_ts = None          # arrival time of this sender's first end marker

    def run(self):
        while True:
            item = self.q.get()
            if item is None:
                return
            data, now = item
            if len(data) < HEADER_SIZE:
                self.malformed += 1
                continue
            seq, _ = struct.unpack(HEADER_FORMAT, data[:HEADER_SIZE])
            if seq == EOF_SEQ:
                if len(data) >= HEADER_SIZE + 4:
                    self.expected = struct.unpack("!I", data[HEADER_SIZE:HEADER_SIZE + 4])[0]
                if self.eof_ts is None:
                    self.eof_ts = now
                continue
            if self.first_ts is None:
                self.first_ts = now
            if self.prev_ts is not None:
                gap = now - self.prev_ts
                if gap > OUTAGE_GAP_S:
                    self.outage_s += gap
            self.prev_ts = now
            self.last_ts = now
            if seq not in self.seen:
                self.seen.add(seq)
                if seq < self.highest:
                    self.out_of_order += 1
                self.highest = max(self.highest, seq)
                self.rx_packets += 1
                self.rx_bytes += len(data)

    def finish(self, m_start, m_end, fallback_duration):
        """Final per-sender numbers (call after the thread has been joined)."""
        outage = self.outage_s
        if m_end is not None:
            # Count an unresolved trailing interruption up to the end of traffic.
            if self.last_ts is not None:
                trailing = max(0.0, m_end - max(self.last_ts, m_start))
                if trailing > OUTAGE_GAP_S:
                    outage += trailing
            else:
                outage = max(0.0, m_end - m_start)
        expected = self.expected
        if expected is None:
            expected = (max(self.seen) + 1) if self.seen else 0
        lost = max(0, expected - self.rx_packets)
        loss_pct = round(100.0 * lost / expected, 2) if expected else 0.0
        active = max(0.001, (m_end - m_start) if m_end is not None else fallback_duration)
        return {
            "sender": self.label,
            "app_received": self.rx_packets,
            "expected_packets": expected,
            "expected_source": "end_marker" if self.expected is not None else "estimated_from_max_seq",
            "packets_lost": lost,
            "loss_percent": loss_pct,
            "rx_bytes": self.rx_bytes,
            "app_throughput_bps": int(self.rx_bytes * 8 / active),
            "outage_s": round(outage, 2),
            "out_of_order_packets": self.out_of_order,
            "reordering_percent": round(100.0 * self.out_of_order / self.rx_packets, 2) if self.rx_packets else 0.0,
            "malformed_packets": self.malformed,
        }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--ip", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=9999)
    parser.add_argument("--duration", type=float, default=15.0)
    parser.add_argument("--out", default="results/last_receiver.json")
    parser.add_argument("--measurement-start-file", default=None,
                        help="JSON file containing start_ts and duration_s, written when sender starts")
    args = parser.parse_args()

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind((args.ip, args.port))
    sock.settimeout(0.5)

    workers = {}                 # "ip:port" -> SenderWorker
    short = 0                    # datagrams too short to carry a sender header
    deadline = time.time() + args.duration + 4.0

    while time.time() < deadline:
        # Stop when every sender seen so far has sent its end marker and the grace period is over.
        if workers and all(w.eof_ts is not None and time.time() >= w.eof_ts + EOF_GRACE_S
                           for w in workers.values()):
            break
        try:
            data, addr = sock.recvfrom(65535)
        except socket.timeout:
            # Do not stop early on silence: a failed link can stay down until the end.
            continue
        now = time.time()
        label = f"{addr[0]}:{addr[1]}"
        w = workers.get(label)
        if w is None:
            w = SenderWorker(label)
            workers[label] = w
            w.start()
        w.q.put((data, now))

    sock.close()
    for w in workers.values():
        w.q.put(None)            # sentinel: process what is queued, then stop
    for w in workers.values():
        w.join()

    m_start = m_end = None
    if args.measurement_start_file:
        try:
            with open(args.measurement_start_file) as f:
                measurement = json.load(f)
            m_start = float(measurement["start_ts"])
            m_end = m_start + float(measurement["duration_s"])
        except (OSError, ValueError, KeyError, TypeError):
            pass

    per_sender = {label: w.finish(m_start, m_end, args.duration) for label, w in workers.items()}

    if per_sender:
        rx_packets = sum(s["app_received"] for s in per_sender.values())
        expected = sum(s["expected_packets"] for s in per_sender.values())
        rx_bytes = sum(s["rx_bytes"] for s in per_sender.values())
        out_of_order = sum(s["out_of_order_packets"] for s in per_sender.values())
        malformed = sum(s["malformed_packets"] for s in per_sender.values())
        outage = max(s["outage_s"] for s in per_sender.values())
    else:
        rx_packets = expected = rx_bytes = out_of_order = malformed = 0
        outage = round(max(0.0, m_end - m_start), 2) if m_end is not None else 0.0
    lost = max(0, expected - rx_packets)
    active = max(0.001, (m_end - m_start) if m_end is not None else args.duration)

    out_dir = os.path.dirname(args.out)
    if out_dir:
        os.makedirs(out_dir, exist_ok=True)
    with open(args.out, "w") as f:
        json.dump({
            "app_received": rx_packets,
            "expected_packets": expected,
            "packets_lost": lost,
            "loss_percent": round(100.0 * lost / expected, 2) if expected else 0.0,
            "rx_bytes": rx_bytes,
            "app_throughput_bps": int(rx_bytes * 8 / active),
            "outage_s": round(outage, 2),
            "outage_method": "interarrival_gaps_plus_trailing_window" if m_end is not None else "interarrival_gaps_only",
            "out_of_order_packets": out_of_order,
            "reordering_percent": round(100.0 * out_of_order / rx_packets, 2) if rx_packets else 0.0,
            "malformed_packets": malformed,
            "sender_count": len(per_sender),
            "senders": per_sender,
        }, f)


if __name__ == "__main__":
    main()
