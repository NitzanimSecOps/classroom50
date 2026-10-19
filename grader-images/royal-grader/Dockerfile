# Grader sandbox for 7.2.0.1 "Royal Mess".
# Gives a PID namespace so the student's system-wide process kills stay contained.
# Baked at boot into the VMSS runners (like arp-grader), built: docker build -t royal-grader:latest .
FROM python:3.12-slim
RUN apt-get update && apt-get install -y --no-install-recommends \
        procps \
        lsof \
    && rm -rf /var/lib/apt/lists/*
# bash + python3 are already in the base image.
