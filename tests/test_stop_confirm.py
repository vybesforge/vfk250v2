#!/usr/bin/env python3
"""The stop tool must PROVE zero, per channel, from the box's own reports.

A stop that says "STOPPED" because it sent the command is the failure this guards
against. `PW` never appears in a read-all, so a write echo is the only witness —
and a PW echo carries no channel id, which is why each channel is selected, zeroed
and confirmed in turn. Confirming only the last channel leaves the other unproven.

Drives the REAL `k250_stop.main()` against a stub BLE client; no radio, no box.

Run: python3 tests/test_stop_confirm.py
"""
import asyncio
import io
import json
import os
import sys
from contextlib import redirect_stdout

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "skill", "vfk250", "scripts"))

try:
    import bleak  # noqa: F401
except ModuleNotFoundError:                  # the fake client replaces it anyway
    import types
    sys.modules["bleak"] = types.ModuleType("bleak")

import k250_stop as S                        # noqa: E402


class FakeClient:
    """A box that echoes its writes back. Configure via the class attrs.

    ca       - the channel-status array the box reports
    mode     - "echo" (answer the written PW), "silent" (no PW report at all),
               "wrong" (always answer 3 regardless)
    pw_for   - {channel_index: value} to answer something else for a channel
    """
    ca = ["Active", "Unplugged", "Unplugged", "Unplugged"]
    mode = "echo"
    pw_for = {}

    def __init__(self, *a, **k):
        self.cb = None
        self.sel = 0

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def start_notify(self, _chr, cb):
        self.cb = cb

    async def write_gatt_char(self, _chr, payload, response=True):
        obj = json.loads(payload.decode())
        if "CA" in obj:
            self.cb(None, json.dumps({"CA": self.ca}).encode())
        elif "AC" in obj:
            self.sel = int(obj["AC"])
            self.cb(None, json.dumps({"AC": self.sel}).encode())
        elif "PW" in obj:
            if FakeClient.mode == "silent":
                return
            if FakeClient.mode == "wrong":
                val = 3
            else:
                val = FakeClient.pw_for.get(self.sel, int(obj["PW"]))
            self.cb(None, json.dumps({"PW": val}).encode())


class FakeDev:
    address = "AA:BB:CC:11:22:33"             # documented placeholder, never real
    name = "Kx250-4S"


async def _find(*a, **k):
    return FakeDev()


_REAL_SLEEP = asyncio.sleep


async def _fast(t, *a):
    return await _REAL_SLEEP(min(t, 0.02))


def drive(**kw):
    """Run the REAL main() against the fake box; return (exit_code, stdout)."""
    for k, v in kw.items():
        setattr(FakeClient, k, v)
    saved = (S.find, S.BleakClient, S.kill_players, asyncio.sleep)
    S.find, S.BleakClient, S.kill_players, asyncio.sleep = _find, FakeClient, (lambda: None), _fast
    buf = io.StringIO()
    try:
        with redirect_stdout(buf):
            rc = asyncio.run(S.main())
    finally:
        S.find, S.BleakClient, S.kill_players, asyncio.sleep = saved
        FakeClient.ca = ["Active", "Unplugged", "Unplugged", "Unplugged"]
        FakeClient.mode, FakeClient.pw_for = "echo", {}
    return rc, buf.getvalue()


def main():
    checks, fails = [], []

    def check(label, ok, note=None):
        checks.append((label, ok, note))
        if not ok:
            fails.append(label)

    rc, out = drive()
    check("one channel, box reports zero -> exit 0", rc == 0, f"exit={rc}")
    check("one channel -> says confirmed by the box", "confirmed by the box" in out)
    check("one channel -> no UNCONFIRMED line", "UNCONFIRMED" not in out)

    rc, out = drive(mode="silent")
    check("no report at all -> exit 1", rc == 1, f"exit={rc}")
    check("no report -> UNCONFIRMED", "UNCONFIRMED" in out)
    check("no report -> how to kill it physically", "hold any knob" in out)

    rc, out = drive(mode="wrong")
    check("box answers non-zero -> exit 1", rc == 1, f"exit={rc}")
    check("box answers non-zero -> UNCONFIRMED", "UNCONFIRMED" in out)
    check("box answers non-zero -> never claims success", "confirmed by the box" not in out)

    # THE PER-CHANNEL CASE: two channels live, ch1 reports zero, ch2 does not.
    rc, out = drive(ca=["Active", "Active", "Unplugged", "Unplugged"],
                    pw_for={1: 5})
    check("two channels, one unconfirmed -> exit 1", rc == 1, f"exit={rc}")
    check("two channels -> names the unconfirmed channel", "channel(s) [2]" in out, out.strip()[:80])
    check("two channels -> does NOT claim success", "confirmed by the box" not in out)

    # ...and with both answering zero it must pass on both, not just the last.
    rc, out = drive(ca=["Active", "Active", "Unplugged", "Unplugged"])
    check("two channels, both zero -> exit 0", rc == 0, f"exit={rc}")
    check("two channels, both zero -> confirms both", "channels [1, 2]" in out, out.strip()[:80])

    for label, ok, note in checks:
        print(f"  {'ok  ' if ok else 'FAIL'}  {label}" + (f"  — {note}" if note else ""))
    print()
    if fails:
        print(f"STOP CONFIRM: FAIL ({len(fails)} of {len(checks)} checks)")
        return 1
    print(f"STOP CONFIRM: PASS — all {len(checks)} checks")
    return 0


if __name__ == "__main__":
    sys.exit(main())