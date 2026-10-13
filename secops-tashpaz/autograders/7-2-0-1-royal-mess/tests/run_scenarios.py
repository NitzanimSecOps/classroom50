#!/usr/bin/env python3
"""
Royal Mess chaos harness — runs INSIDE a rootless PID namespace
(`unshare -Urpf --mount-proc python3 run_scenarios.py <cleanup.sh>`), so the
student's system-wide process kills only ever touch this sandbox, never the host.

Spawns the four messes + a legit bystander, runs the student's cleanup.sh,
measures the final state, and prints one JSON line. This process is PID 1 of the
namespace, so it reaps orphaned zombies after the student kills their parent.
"""
import json
import os
import shutil
import socket
import subprocess
import sys
import time

CLEANUP = sys.argv[1]
PORT = 8077


def spawn(args):
    return subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def port_free(port):
    s = socket.socket()
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        s.bind(("127.0.0.1", port))
        return True
    except OSError:
        return False
    finally:
        s.close()


def reap():
    """As PID 1, reap zombies reparented to us after the student killed a parent."""
    while True:
        try:
            pid, _ = os.waitpid(-1, os.WNOHANG)
            if pid == 0:
                break
        except ChildProcessError:
            break


def zombie_count():
    n = 0
    for d in os.listdir("/proc"):
        if not d.isdigit():
            continue
        try:
            with open(f"/proc/{d}/stat") as f:
                state = f.read().split(") ", 1)[1].split(" ", 1)[0]
            if state == "Z":
                n += 1
        except (OSError, IndexError):
            pass
    return n


def curls_alive():
    return subprocess.run(["pgrep", "-x", "curl"], capture_output=True).returncode == 0


def main():
    wd = "/tmp/rm"
    os.makedirs(wd, exist_ok=True)
    procs = {}
    procs["port"] = spawn([sys.executable, "-c",
        f"import socket,time;s=socket.socket();s.setsockopt(1,2,1);"
        f"s.bind(('127.0.0.1',{PORT}));s.listen();time.sleep(600)"])
    procs["hog"] = spawn(["bash", "-c", "while :; do :; done"])
    fakecurl = os.path.join(wd, "curl")          # a binary named 'curl' so comm=='curl'
    shutil.copy(shutil.which("sleep"), fakecurl)
    os.chmod(fakecurl, 0o755)
    flood = os.path.join(wd, "royal_views.sh")
    with open(flood, "w") as f:
        f.write(f"#!/usr/bin/env bash\nwhile true; do '{fakecurl}' 60 & sleep 1; done\n")
    procs["flood"] = spawn(["bash", flood])
    procs["zomb"] = spawn([sys.executable, "-c",
        "import os,time\nfor _ in range(5):\n    if os.fork()==0: os._exit(0)\nwhile True: time.sleep(1)"])
    procs["bystander"] = spawn(["bash", "-c", "exec -a royal_db sleep 600"])

    time.sleep(3)  # let services bind, zombies form, curls spawn

    subprocess.run(["bash", CLEANUP, str(PORT)], cwd=wd, capture_output=True, timeout=60)

    time.sleep(3)  # let a surviving flood parent respawn; let kills settle
    for p in procs.values():
        p.poll()
    reap()

    print(json.dumps({
        "port_free": port_free(PORT),
        "hog_dead": procs["hog"].poll() is not None,
        "flood_gone": procs["flood"].poll() is not None and not curls_alive(),
        "zombies": zombie_count(),
        "bystander_alive": procs["bystander"].poll() is None,
    }))


if __name__ == "__main__":
    main()
