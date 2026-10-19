#!/usr/bin/env python3
"""Grade 6.1.0.2 inside the sandbox: import the student's resolve_ip, call it in the
attacker namespace against each endpoint, and check the MAC it returns.

This is deliberately NOT blackbox — returning the right MAC proves the student both
SENT an ARP request and PARSED the reply (the parse can't be seen on the wire). Prints
{scenario: {"ok": bool, "detail": str}} as JSON.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile

import lab
from lab import ATTACKER, GATEWAY, VICTIM


def measure(code_dir: str) -> dict:
    lab.build()
    work = tempfile.mkdtemp(prefix="student-")
    try:
        for f in os.listdir(code_dir):
            if f.endswith(".py"):
                shutil.copy2(os.path.join(code_dir, f), os.path.join(work, f))
        with open(os.path.join(work, "config.json"), "w") as fh:
            json.dump({"iface": ATTACKER.host_if, "endpoints": [VICTIM.ip, GATEWAY.ip]}, fh)

        # Call resolve_ip for each endpoint, inside the attacker ns (where the socket +
        # interface live). Catch per-call errors so one bad answer doesn't hide the other.
        helper = (
            "import json, sys\n"
            "sys.path.insert(0, '.')\n"
            "res = {}\n"
            "try:\n"
            "    from main import resolve_ip\n"
            f"    for name, ip in (('gateway', '{GATEWAY.ip}'), ('victim', '{VICTIM.ip}')):\n"
            "        try:\n"
            "            res[name] = resolve_ip(ip)\n"
            "        except Exception as e:\n"
            "            res[name] = f'ERROR: {type(e).__name__}: {e}'\n"
            "except Exception as e:\n"
            "    res['import'] = f'ERROR: {type(e).__name__}: {e}'\n"
            "print(json.dumps(res))\n"
        )
        proc = subprocess.run(
            ["ip", "netns", "exec", ATTACKER.name, sys.executable, "-c", helper],
            cwd=work, capture_output=True, text=True, timeout=60)

        lines = [ln for ln in (proc.stdout or "").splitlines() if ln.strip().startswith("{")]
        res = json.loads(lines[-1]) if lines else {}

        def check(name: str, want_mac: str) -> dict:
            got = res.get(name)
            if isinstance(got, str) and got.lower() == want_mac.lower():
                return {"ok": True, "detail": f"resolve_ip returned {got}"}
            if got is None:
                why = res.get("import") or (proc.stderr or "").strip()[-300:] or "no value returned"
                return {"ok": False, "detail": f"no MAC for {name}: {why}"}
            return {"ok": False, "detail": f"resolve_ip returned {got!r} (want {want_mac})"}

        return {
            "resolves_gateway": check("gateway", GATEWAY.mac),
            "resolves_victim": check("victim", VICTIM.mac),
        }
    finally:
        lab.teardown()


if __name__ == "__main__":
    print(json.dumps(measure(sys.argv[1] if len(sys.argv) > 1 else "/code")))
