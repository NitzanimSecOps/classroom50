#!/usr/bin/env python3
"""
Royal Mess chaos harness — runs INSIDE a docker container (its own PID namespace),
so the student's system-wide process kills stay contained.

It writes TRUSTED copies of the mess scripts (a tampered student copy can't weaken
the mess), runs boogie.sh to create King Julian's four messes, then runs the
student's cleanup.sh, measures the final state, and prints one JSON line. As PID 1
it reaps zombies reparented to it.
"""
import json
import os
import socket
import subprocess
import sys
import time

CLEANUP = sys.argv[1]
PORT = 8000

SCRIPTS = {
    "boogie.sh": r'''#!/bin/bash
echo "Operation: Royal Mess initiated..."
nohup python3 royal_server_v1.py > /dev/null 2>&1 &
cp $(which python3) ./julien_dance_party
nohup ./julien_dance_party juliens_dance.py > /dev/null 2>&1 &
nohup ./viral_video.sh > /dev/null 2>&1 &
cp $(which python3) ./royal_cleaner
nohup ./royal_cleaner clean_the_mess.py > /dev/null 2>&1 &
''',
    "royal_server_v1.py": r'''import http.server
import socketserver
PORT = 8000
socketserver.TCPServer.allow_reuse_address = True
with socketserver.TCPServer(("", PORT), http.server.SimpleHTTPRequestHandler) as httpd:
    httpd.serve_forever()
''',
    "juliens_dance.py": r'''import hashlib, signal, time
def h(sig, frame):
    pass
signal.signal(signal.SIGINT, h)
signal.signal(signal.SIGTERM, h)
while True:
    hashlib.sha256(b"I like to move it move it").hexdigest()
''',
    "viral_video.sh": r'''#!/bin/bash
TARGET="http://localhost:8000"
while true; do
    curl -s -o /dev/null "$TARGET" > /dev/null 2>&1
done
''',
    "clean_the_mess.py": r'''import os, sys, time
for i in range(10):
    if os.fork() == 0:
        sys.exit(0)
while True:
    time.sleep(10)
''',
}


def port_free(port):
    s = socket.socket()
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        s.bind(("", port))
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


def _pgrep_f(pat):
    return subprocess.run(["pgrep", "-f", pat], capture_output=True).returncode == 0


def _pgrep_x(name):
    return subprocess.run(["pgrep", "-x", name], capture_output=True).returncode == 0


def main():
    wd = "/tmp/rm"
    os.makedirs(wd, exist_ok=True)
    for name, content in SCRIPTS.items():
        p = os.path.join(wd, name)
        with open(p, "w") as f:
            f.write(content)
        if name.endswith(".sh"):
            os.chmod(p, 0o755)

    subprocess.run(["bash", os.path.join(wd, "boogie.sh")], cwd=wd,
                   capture_output=True, timeout=30)
    time.sleep(5)  # let the server bind, curls spam, zombies form, CPU spin

    subprocess.run(["bash", CLEANUP, str(PORT)], cwd=wd, capture_output=True, timeout=60)
    time.sleep(3)  # let a surviving flood respawn; let kills settle
    reap()

    print(json.dumps({
        "port_free": port_free(PORT),
        "cpu_hog_gone": not _pgrep_f("julien_dance_party"),
        "curls_stopped": not _pgrep_f("viral_video.sh") and not _pgrep_x("curl"),
        "zombies": zombie_count(),
    }))


if __name__ == "__main__":
    main()
