#!/usr/bin/env python3
"""
Royal Mess chaos harness — runs INSIDE a docker container (its own PID namespace),
so the student's system-wide process kills stay contained.

It writes a TRUSTED copy of boogie.sh (a tampered student copy can't weaken the mess),
runs it to create the four messes, then runs the student's cleanup.sh, measures the
final state, and prints one JSON line. As PID 1 it reaps zombies reparented to it.
"""
import json
import os
import socket
import subprocess
import sys
import time

CLEANUP = sys.argv[1]
PORT = 8077

# Identical to the boogie.sh shipped to students (template/), kept trusted here.
BOOGIE = r'''#!/usr/bin/env bash
PORT="${1:-8077}"
nohup python3 -c "import socket,time;s=socket.socket();s.setsockopt(1,2,1);s.bind(('127.0.0.1',$PORT));s.listen(16);time.sleep(99999)" >/dev/null 2>&1 &
nohup bash -c 'while :; do :; done' >/dev/null 2>&1 &
_d="$(mktemp -d)"; cp "$(command -v sleep)" "$_d/curl"; chmod +x "$_d/curl"
nohup bash -c "while true; do '$_d/curl' 60 & sleep 1; done" >/dev/null 2>&1 &
nohup python3 -c "import os,time
for _ in range(5):
    if os.fork()==0: os._exit(0)
while True: time.sleep(1)" >/dev/null 2>&1 &
'''


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


def top_cpu():
    out = subprocess.run(["ps", "-eo", "%cpu", "--sort=-%cpu", "--no-headers"],
                         capture_output=True, text=True).stdout.splitlines()
    for line in out:
        try:
            return float(line.strip())
        except ValueError:
            continue
    return 0.0


def curls_alive():
    return subprocess.run(["pgrep", "-x", "curl"], capture_output=True).returncode == 0


def main():
    wd = "/tmp/rm"
    os.makedirs(wd, exist_ok=True)
    boogie = os.path.join(wd, "boogie.sh")
    with open(boogie, "w") as f:
        f.write(BOOGIE)

    subprocess.run(["bash", boogie, str(PORT)], cwd=wd, capture_output=True, timeout=30)
    time.sleep(4)  # let curls spawn + zombies form

    subprocess.run(["bash", CLEANUP, str(PORT)], cwd=wd, capture_output=True, timeout=60)
    time.sleep(3)  # let a surviving flood respawn; let kills settle
    reap()

    print(json.dumps({
        "port_free": port_free(PORT),
        "cpu_hog_gone": top_cpu() < 50.0,
        "curls_gone": not curls_alive(),
        "zombies": zombie_count(),
    }))


if __name__ == "__main__":
    main()
