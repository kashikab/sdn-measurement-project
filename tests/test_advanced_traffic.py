"""Loopback tests; do not require Mininet or root."""
import json
import pathlib
import subprocess
import sys
import tempfile
import time
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
SENDER = ROOT / 'app' / 'sender.py'
RECEIVER = ROOT / 'app' / 'receiver.py'

class TrafficTests(unittest.TestCase):
    def test_seeded_rate_windows_and_delivery(self):
        with tempfile.TemporaryDirectory() as directory:
            receiver_path = pathlib.Path(directory) / 'rx.json'
            sender_path = pathlib.Path(directory) / 'tx.json'
            import socket
            with socket.socket() as s:
                s.bind(('127.0.0.1', 0))
                port = s.getsockname()[1]
            rx = subprocess.Popen([sys.executable, str(RECEIVER), '--ip', '127.0.0.1', '--port', str(port), '--duration', '2', '--out', str(receiver_path)])
            try:
                time.sleep(0.15)
                subprocess.run([sys.executable, str(SENDER), '--ip', '127.0.0.1', '--port', str(port), '--duration', '2', '--rate-pattern', 'random', '--rate-min', '300', '--rate-max', '2000', '--pattern', 'random', '--seed', '42', '--out', str(sender_path)], check=True, timeout=12)
                rx.wait(timeout=12)
                sent = json.loads(sender_path.read_text())
                received = json.loads(receiver_path.read_text())
                self.assertEqual(sent['rate_pattern'], 'random')
                self.assertEqual([w['target_pps'] for w in sent['rate_windows']], [1555, 1707])
                self.assertGreater(sent['app_sent'], 0)
                self.assertEqual(sent['expected_switch_packets'], sent['app_sent'] + 5)
                self.assertEqual(sent['packet_reordering'], 'disabled')
                self.assertEqual(received['app_received'], sent['app_sent'])
            finally:
                if rx.poll() is None:
                    rx.kill()
                    rx.wait()

    def test_optional_reordering_and_receiver_detection(self):
        import socket
        with tempfile.TemporaryDirectory() as directory:
            receiver_path = pathlib.Path(directory) / 'rx.json'
            sender_path = pathlib.Path(directory) / 'tx.json'
            with socket.socket() as sock:
                sock.bind(('127.0.0.1', 0))
                port = sock.getsockname()[1]
            rx = subprocess.Popen([sys.executable, str(RECEIVER), '--ip', '127.0.0.1', '--port', str(port), '--duration', '1', '--out', str(receiver_path)])
            try:
                time.sleep(0.15)
                subprocess.run([sys.executable, str(SENDER), '--ip', '127.0.0.1', '--port', str(port), '--rate', '300', '--duration', '1', '--reorder', '--seed', '42', '--out', str(sender_path)], check=True, timeout=10)
                rx.wait(timeout=10)
                sent = json.loads(sender_path.read_text())
                received = json.loads(receiver_path.read_text())
                self.assertEqual(sent['packet_reordering'], 'enabled')
                self.assertGreater(sent['source_reordered_positions'], 0)
                self.assertEqual(received['app_received'], sent['app_sent'])
                self.assertGreater(received['out_of_order_packets'], 0)
                self.assertEqual(received['packets_lost'], 0)
            finally:
                if rx.poll() is None:
                    rx.kill()
                    rx.wait()

    def test_invalid_rate_range(self):
        with tempfile.TemporaryDirectory() as directory:
            proc = subprocess.run([sys.executable, str(SENDER), '--ip', '127.0.0.1', '--rate-pattern', 'random', '--rate-min', '2000', '--rate-max', '300', '--duration', '1', '--out', str(pathlib.Path(directory) / 'tx.json')], capture_output=True, text=True, timeout=5)
            self.assertNotEqual(proc.returncode, 0)
            self.assertIn('rate range', proc.stderr)

    def _free_port(self):
        import socket
        with socket.socket() as s:
            s.bind(('127.0.0.1', 0))
            return s.getsockname()[1]

    def test_two_senders_are_counted_separately(self):
        with tempfile.TemporaryDirectory() as directory:
            rx_path = pathlib.Path(directory) / 'rx.json'
            port = self._free_port()
            rx = subprocess.Popen([sys.executable, str(RECEIVER), '--ip', '127.0.0.1', '--port', str(port), '--duration', '3', '--out', str(rx_path)])
            try:
                time.sleep(0.15)
                senders = [subprocess.Popen([sys.executable, str(SENDER), '--ip', '127.0.0.1', '--port', str(port), '--rate', '100', '--duration', '2', '--out', str(pathlib.Path(directory) / f'tx{i}.json')]) for i in range(2)]
                for s in senders:
                    s.wait(timeout=12)
                rx.wait(timeout=12)
                result = json.loads(rx_path.read_text())
                self.assertEqual(result['sender_count'], 2)
                self.assertEqual(result['app_received'], 400)
                self.assertEqual(result['packets_lost'], 0)
                for per_sender in result['senders'].values():
                    self.assertEqual(per_sender['app_received'], 200)
                    self.assertEqual(per_sender['expected_packets'], 200)
            finally:
                if rx.poll() is None:
                    rx.kill()
                    rx.wait()

    def test_silence_in_the_middle_is_an_outage_not_the_end(self):
        import socket
        import struct
        with tempfile.TemporaryDirectory() as directory:
            rx_path = pathlib.Path(directory) / 'rx.json'
            port = self._free_port()
            rx = subprocess.Popen([sys.executable, str(RECEIVER), '--ip', '127.0.0.1', '--port', str(port), '--duration', '6', '--out', str(rx_path)])
            try:
                time.sleep(0.15)
                s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                dest = ('127.0.0.1', port)
                seq = 0
                for _ in range(20):
                    s.sendto(struct.pack('!Id', seq, time.time()) + b'x' * 100, dest); seq += 1; time.sleep(0.01)
                time.sleep(3.2)   # longer than the old 2.5 s silence rule
                for _ in range(20):
                    s.sendto(struct.pack('!Id', seq, time.time()) + b'x' * 100, dest); seq += 1; time.sleep(0.01)
                for _ in range(5):
                    s.sendto(struct.pack('!Id', 0xFFFFFFFF, time.time()) + struct.pack('!I', seq), dest); time.sleep(0.02)
                rx.wait(timeout=12)
                result = json.loads(rx_path.read_text())
                self.assertEqual(result['app_received'], 40)
                self.assertEqual(result['packets_lost'], 0)
                self.assertGreater(result['outage_s'], 3.0)
            finally:
                if rx.poll() is None:
                    rx.kill()
                    rx.wait()

if __name__ == '__main__':
    unittest.main()
