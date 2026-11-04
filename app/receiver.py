#!/usr/bin/env python3
import socket
import time
import struct
import argparse
import os
import csv

HEADER_FORMAT = '!IId'
HEADER_SIZE = struct.calcsize(HEADER_FORMAT)

def run_receiver(listen_ip, listen_port, timeout, csv_path):
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind((listen_ip, listen_port))
    sock.settimeout(1.0)

    os.makedirs(os.path.dirname(csv_path), exist_ok=True)
    csv_file = open(csv_path, 'w', newline='')
    writer = csv.writer(csv_file)
    writer.writerow([
        'elapsed_sec', 'rx_packets', 'lost_packets', 'loss_pct',
        'app_goodput_mbps', 'app_wire_equiv_mbps', 'avg_latency_ms', 'jitter_ms'
    ])

    print(f"[Receiver] Listening on {listen_ip}:{listen_port}...")

    start_time = None
    last_pkt_time = None
    window_start = None

    rx_packets = 0
    expected_seq = {}  # Track sequence per sender IP
    lost_packets = 0

    window_bytes = 0
    window_wire_bytes = 0
    latencies = []
    last_transit = None
    jitter = 0.0

    try:
        while True:
            try:
                data, addr = sock.recvfrom(65535)
            except socket.timeout:
                if start_time and (time.time() - last_pkt_time > timeout):
                    print("[Receiver] Inactivity timeout reached. Closing session.")
                    break
                continue

            now = time.time()
            if len(data) < HEADER_SIZE:
                continue

            seq, pkt_size, send_ts = struct.unpack(HEADER_FORMAT, data[:HEADER_SIZE])

            # Check for EOF packet
            if seq == 0xFFFFFFFF:
                continue

            if start_time is None:
                start_time = now
                window_start = now

            last_pkt_time = now
            sender_ip = addr[0]

            # Per-sender sequence tracking for accurate packet loss
            if sender_ip not in expected_seq:
                expected_seq[sender_ip] = seq
            if seq > expected_seq[sender_ip]:
                lost_packets += (seq - expected_seq[sender_ip])
                expected_seq[sender_ip] = seq + 1
            else:
                expected_seq[sender_ip] = max(expected_seq[sender_ip], seq + 1)

            rx_packets += 1
             rx_len = len(data)
            window_bytes += rx_len
            # Estimate L2 wire size: UDP payload + 8B UDP + 20B IP + 14B Ethernet = +42 bytes
            window_wire_bytes += (rx_len + 42)

            # One-way latency and RFC 3550 Jitter calculation
            transit = (now - send_ts) * 1000.0  # ms
            latencies.append(transit)
            if last_transit is not None:
                d = abs(transit - last_transit)
                jitter += (d - jitter) / 16.0
            last_transit = transit

            # Log time-series every 1.0 second
            if now - window_start >= 1.0:
                dt = now - window_start
                elapsed = round(now - start_time, 1)
                goodput_mbps = (window_bytes * 8) / (dt * 1e6)
                wire_mbps = (window_wire_bytes * 8) / (dt * 1e6)
                total_expected = rx_packets + lost_packets
                loss_pct = (lost_packets / total_expected * 100.0) if total_expected > 0 else 0.0
                avg_lat = sum(latencies) / len(latencies) if latencies else 0.0

                writer.writerow([
                    elapsed, rx_packets, lost_packets, round(loss_pct, 2),
                    round(goodput_mbps, 3), round(wire_mbps, 3),
                    round(avg_lat, 2), round(jitter, 2)
                ])
                csv_file.flush()

                window_start = now
                window_bytes = 0
                window_wire_bytes = 0
                latencies.clear()

    except KeyboardInterrupt:
        pass
    finally:
        total_expected = rx_packets + lost_packets
        loss_pct = (lost_packets / total_expected * 100.0) if total_expected > 0 else 0.0
        print(f"[Receiver] Summary: RX={rx_packets} | Lost={lost_packets} ({loss_pct:.2f}%) | Jitter={jitter:.2f}ms")
        csv_file.close()
        sock.close()

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description="UDP Bandwidth & QoS Receiver")
    parser.add_argument('--ip', default='0.0.0.0', help='Bind IP')
    parser.add_argument('--port', type=int, default=9999, help='Bind Port')
    parser.add_argument('--timeout', type=float, default=4.0, help='Auto-stop timeout (s)')
    parser.add_argument('--csv', default='results/app_receiver.csv', help='Output CSV path')
    args = parser.parse_args()

    run_receiver(args.ip, args.port, args.timeout, args.csv)
