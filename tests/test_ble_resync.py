#!/usr/bin/env python3
"""The BLE reader must deliver EVERY report, and never wedge on a bad packet.

Two failure modes this guards:

  * a frame that could never parse used to sit in front of every later frame, so
    nothing parsed again and `last` froze at the last good state; and
  * returning only the NEWEST object from a buffer silently dropped the other
    when two reports arrived together — and a dropped zero report is exactly what
    a stop must never miss.

`_take_objects()` drains all complete objects and re-syncs past junk. The K250
reader counts every PW report (`pw_seen` / `pw_last`) so a confirmation can wait
for a FRESH report rather than trusting whatever happens to be in `last`.

Run: python3 tests/test_ble_resync.py
"""
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "skill", "vfk250", "scripts"))
from k250_ble import K250, _take_objects           # noqa: E402


def main():
    checks, fails = [], []

    def check(label, ok, note=None):
        checks.append((label, ok, note))
        if not ok:
            fails.append(label)

    objs, rest = _take_objects('{"PW": 0}')
    check("a whole object is delivered", objs == [{"PW": 0}], objs)
    check("a whole object leaves nothing behind", rest == "", repr(rest))

    objs, rest = _take_objects('{"AC": 0, "MA"')
    check("a split frame is not delivered yet", objs == [], objs)
    check("a split frame is KEPT", rest == '{"AC": 0, "MA"', repr(rest))
    objs, rest = _take_objects(rest + ': 0}')
    check("the rest of a split frame then parses", objs == [{"AC": 0, "MA": 0}], objs)

    # THE DROPPED-REPORT CASE: two reports in one packet must BOTH arrive.
    objs, rest = _take_objects('{"PW": 3}{"PW": 0}')
    check("two reports together -> BOTH delivered", objs == [{"PW": 3}, {"PW": 0}], objs)
    check("two reports together -> nothing left", rest == "", repr(rest))

    objs, rest = _take_objects('\x01\x02nonsense')
    check("brace-less junk is dropped", objs == [] and rest == "", (objs, rest))

    objs, rest = _take_objects('??{"PW": 0}')
    check("junk in front of a frame still yields the frame", objs == [{"PW": 0}], objs)

    objs, rest = _take_objects("{" + "x" * 9000)
    check("an over-long unparseable buffer is dropped", objs == [] and rest == "", repr(rest[:20]))

    # ---- the reader: pw_seen counts EVERY report, not just the newest ----
    class C:
        def start_notify(self, *a):
            pass

    k = K250(C())
    k._on_notify(None, b'{"PW": 3}{"PW": 0}')
    check("reader counts both PW reports", k.pw_seen == 2, k.pw_seen)
    check("reader keeps the newest PW value", k.pw_last == 0, k.pw_last)
    check("reader keeps the newest message", k.last == {"PW": 0}, k.last)

    k2 = K250(C())
    k2._on_notify(None, b'\x00garbage')
    check("reader survives junk", k2.pw_seen == 0, k2.pw_seen)
    k2._on_notify(None, b'{"PW": 0}')
    check("reader recovers after junk", (k2.pw_seen, k2.pw_last) == (1, 0), (k2.pw_seen, k2.pw_last))

    k3 = K250(C())
    k3._on_notify(None, b'{"PW": 0}{"AC": 0}')
    check("a zero followed by another message is still counted", k3.pw_seen == 1, k3.pw_seen)
    check("...and its value is kept despite a later message", k3.pw_last == 0, k3.pw_last)

    for label, ok, note in checks:
        print(f"  {'ok  ' if ok else 'FAIL'}  {label}" + (f"  — {note}" if note else ""))
    print()
    if fails:
        print(f"BLE RESYNC: FAIL ({len(fails)} of {len(checks)} checks)")
        return 1
    print(f"BLE RESYNC: PASS — all {len(checks)} checks")
    return 0


if __name__ == "__main__":
    sys.exit(main())