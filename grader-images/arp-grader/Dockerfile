# Grading sandbox for the Unit-6 ARP exercises (Levels 2-4). Baked into each runner
# instance at boot (see cloud-init.yaml.tftpl). At grade time test_exercise_*.py runs it
# as `docker run --privileged --network none`, so the student's code gets the capabilities
# to build a 3-host network-namespace lab but NO route off the box (which is also the
# no-internet / no-AI backstop during grading). The image carries only the toolchain; the
# netns lab (lab.py) and the student's code are built/mounted at grade time.
FROM python:3.12-slim
RUN apt-get update && apt-get install -y --no-install-recommends \
        iproute2 ethtool iputils-ping procps \
    && rm -rf /var/lib/apt/lists/* \
    && pip install --no-cache-dir scapy
WORKDIR /work
