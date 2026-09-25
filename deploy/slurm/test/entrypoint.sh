#!/bin/bash
# Start munge, slurmctld and slurmd, wait for the node, then run the command
set -euo pipefail
if [ ! -s /etc/munge/munge.key ]; then
    dd if=/dev/urandom bs=1 count=1024 of=/etc/munge/munge.key 2>/dev/null
    chown munge:munge /etc/munge/munge.key
    chmod 400 /etc/munge/munge.key
fi
su -s /bin/sh munge -c "munged"
slurmctld
slurmd
for _ in $(seq 60); do
    if sinfo -h -o %T 2>/dev/null | grep -q idle; then
        exec "$@"
    fi
    sleep 1
done
echo "Slurm node did not become idle" >&2
cat /var/log/slurm/*.log >&2
exit 1
