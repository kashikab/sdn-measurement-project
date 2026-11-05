# Fixes applied

## network/controller.py
- **Critical:** `flow_stats_reply_handler` used undefined `dp` (NameError) -> controller event loop died on first high-rate redirect. Now uses `ev.msg.datapath`; stats and port-status handlers are wrapped in try/except.
- Flow table is cleared on switch connect; `measurement_rule_installed` reset so MODIFY_STRICT works after reconnect.
- ARP (h1 side on s1, h2 side on s3) sent over both paths so it survives primary-link failure.
- Fallback rule on s1 for non-UDP traffic from h1 (ping etc.).

## app/receiver.py
- 0.5 s drain period after first end marker so late/reordered packets are not counted as lost.

## web/server.py
- try/finally teardown of Mininet and Ryu (previously a failed run left Ryu on port 6633).
- Stale sender/receiver JSON removed before each run.
- Input validation (mode, pattern, int seed, fail_at_s, sizes, loss); sender launched with an argv list (no argument injection as root).
- waitConnected instead of fixed sleep; Ryu output logged to results/controller.log; sender exit code checked.
- HTTP: 400 on bad JSON / run id.

## integration/run_demo.py
- Same cleanup, stale-output removal, controller logging, sender exit-code check.

## Second round (receiver concurrency and setup)

### app/receiver.py
- **One worker thread per sender** (keyed by source IP and source port), each with its own queue; the main thread only receives and timestamps. Two senders are now counted separately; before, their packets were merged into one stream (600 sent, 300 reported).
- Output keeps all existing top-level fields (totals over all senders, so `web/server.py` is unchanged) and adds `sender_count` and a per-sender `senders` object.
- Stops when every sender seen has sent its end marker and the 0.5 s drain has passed, or at the deadline. Still never stops on silence.

### tests/test_advanced_traffic.py
- Added `test_two_senders_are_counted_separately` and `test_silence_in_the_middle_is_an_outage_not_the_end` (5 loopback tests in total, no Mininet or root needed).

### web/server.py, integration/run_demo.py
- The controller is started with `RYU_PYTHON` if set, otherwise with the current Python, so Ryu may live in a separate virtual environment from Mininet.

### docs
- `docs/APP_TESTS_AND_README_SECTION.md`: concurrency and no-stop-on-silence described; tests T9 and T10 added. `README.md`: `RYU_PYTHON` explained.

### Not verified here
- Nothing in this round was run inside Mininet or with Ryu; the receiver was tested on loopback only.
