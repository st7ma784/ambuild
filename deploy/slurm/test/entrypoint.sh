#!/bin/bash
# Start munge, slurmctld and slurmd, wait for the node, then run the command
set -euo pipefail
if [ ! -s /etc/munge/munge.key ]; then
    dd if=/dev/urandom bs=1 count=1024 of=/etc/munge/munge.key 2>/dev/null
    chown munge:munge /etc/munge/munge.key
    chmod 400 /etc/munge/munge.key
fi
if [ ! -s /var/spool/slurmctld/jwt_hs256.key ]; then
    dd if=/dev/urandom bs=32 count=1 of=/var/spool/slurmctld/jwt_hs256.key 2>/dev/null
    chmod 600 /var/spool/slurmctld/jwt_hs256.key
fi
su -s /bin/sh munge -c "munged"
slurmctld
slurmd
# slurmrestd, authenticating each request by the JWT it carries (port 6820). SLURM_JWT=daemon
# makes it pass the JWT on to slurmctld (else it uses munge, which rejects it); only the
# slurmctld endpoints, as there is no slurmdbd (accounting) here.
SLURM_JWT=daemon setpriv --reuid=slurmrest --regid=slurmrest --clear-groups \
    slurmrestd -a rest_auth/jwt -s openapi/slurmctld 0.0.0.0:6820 >/var/log/slurm/slurmrestd.log 2>&1 &
for _ in $(seq 60); do
    if sinfo -h -o %T 2>/dev/null | grep -q idle; then
        exec "$@"
    fi
    sleep 1
done
echo "Slurm node did not become idle" >&2
cat /var/log/slurm/*.log >&2
exit 1
