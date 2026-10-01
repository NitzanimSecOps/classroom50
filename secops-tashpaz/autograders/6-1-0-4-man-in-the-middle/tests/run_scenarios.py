#!/usr/bin/env python3
"""Grade 6.1.0.4 inside the sandbox: launch main.py in the attacker namespace, then
measure the full man-in-the-middle from OUTSIDE the code — both caches poisoned, traffic
passing through the attacker, and the victim STILL reaching the gateway (the DoS-vs-MITM
discriminator). Prints {scenario: {"ok": bool, "detail": str}} as JSON.
"""
from __future__ import annotations

import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import time

import lab
from lab import ATTACKER, GATEWAY, VICTIM

SNIFF_SECONDS = 4.0
POISON_TIMEOUT = 12.0
POLL_INTERVAL = 0.4


def _ns(ns: str, *args: str):
    return lab.run("ip", "netns", "exec", ns, *args, check=False)


def sniff_arp_from_attacker(seconds: float) -> bool:
    script = (
        "from scapy.all import sniff, ARP;"
        f"a='{ATTACKER.mac}'.lower();"
        f"ips={{'{GATEWAY.ip}','{VICTIM.ip}'}};"
        "f=lambda p: p.haslayer(ARP) and p[ARP].op in (1, 2) "
        "and p[ARP].hwsrc.lower()==a and p[ARP].psrc in ips;"
        f"pk=sniff(iface='{VICTIM.host_if}',timeout={seconds},lfilter=f,count=1);"
        "print('HIT' if pk else 'MISS')"
    )
    return "HIT" in _ns(VICTIM.name, "python3", "-c", script).stdout


def poll_until_poisoned() -> tuple[str | None, str | None]:
    atk = ATTACKER.mac.lower()
    deadline = time.time() + POISON_TIMEOUT
    while True:
        victim = lab.neigh_mac(VICTIM.name, GATEWAY.ip)
        gateway = lab.neigh_mac(GATEWAY.name, VICTIM.ip)
        if (victim == atk and gateway == atk) or time.time() >= deadline:
            return victim, gateway
        time.sleep(POLL_INTERVAL)


def launch_student(code_dir: str) -> subprocess.Popen:
    work = tempfile.mkdtemp(prefix="student-")
    for name in os.listdir(code_dir):
        if name.endswith(".py"):
            shutil.copy2(os.path.join(code_dir, name), os.path.join(work, name))
    with open(os.path.join(work, "config.json"), "w") as fh:
        json.dump({"iface": ATTACKER.host_if, "endpoints": [VICTIM.ip, GATEWAY.ip]}, fh)
    return subprocess.Popen(
        ["ip", "netns", "exec", ATTACKER.name, sys.executable, "main.py"],
        cwd=work, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def measure(code_dir: str) -> dict:
    lab.build()
    student = None
    try:
        # Prime: model a real LAN where the two already talk (each caches the other's
        # real MAC), so poisoning has an existing entry to override.
        for a, b in ((VICTIM, GATEWAY), (GATEWAY, VICTIM)):
            _ns(a.name, "ping", "-c", "1", "-W", "1", b.ip)

        student = launch_student(code_dir)
        emits = sniff_arp_from_attacker(SNIFF_SECONDS)
        victim_cache, gateway_cache = poll_until_poisoned()

        def victim_to_gateway_with_sniff() -> tuple[bool, bool]:
            script = (
                "from scapy.all import sniff, IP, ICMP;"
                f"f=lambda p: p.haslayer(ICMP) and p.haslayer(IP) "
                f"and p[IP].src=='{VICTIM.ip}' and p[IP].dst=='{GATEWAY.ip}';"
                f"pk=sniff(iface='{ATTACKER.host_if}',timeout={SNIFF_SECONDS},lfilter=f,count=1);"
                "print('HIT' if pk else 'MISS')"
            )
            proc = subprocess.Popen(
                ["ip", "netns", "exec", ATTACKER.name, "python3", "-c", script],
                stdout=subprocess.PIPE, text=True)
            time.sleep(0.5)
            ping = _ns(VICTIM.name, "ping", "-c", "2", "-W", "2", GATEWAY.ip)
            out, _ = proc.communicate(timeout=SNIFF_SECONDS + 3)
            return "HIT" in out, ping.returncode == 0

        intercepted, reachable = victim_to_gateway_with_sniff()
        atk = ATTACKER.mac.lower()

        return {
            "emits_arp_replies": {
                "ok": emits,
                "detail": "attacker emits forged ARP frames on the segment" if emits
                else "no forged ARP frame from the attacker's MAC was seen on the wire",
            },
            "poisons_victim": {
                "ok": victim_cache == atk,
                "detail": f"victim's cache for the gateway = {victim_cache} (want {atk})",
            },
            "poisons_gateway": {
                "ok": gateway_cache == atk,
                "detail": f"gateway's cache for the victim = {gateway_cache} (want {atk})",
            },
            "intercepts_traffic": {
                "ok": intercepted,
                "detail": "victim->gateway traffic passes through the attacker" if intercepted
                else "attacker never saw the victim->gateway packet (not on the path)",
            },
            "preserves_connectivity": {
                "ok": reachable and (victim_cache == atk),
                "detail": "victim still reaches the gateway while poisoned (true MITM, not DoS)"
                if reachable else
                "victim lost connectivity to the gateway (blackholed, not forwarded)",
            },
        }
    finally:
        if student is not None:
            student.send_signal(signal.SIGTERM)
            try:
                student.wait(timeout=5)
            except subprocess.TimeoutExpired:
                student.kill()
        lab.teardown()


if __name__ == "__main__":
    print(json.dumps(measure(sys.argv[1] if len(sys.argv) > 1 else "/code")))
