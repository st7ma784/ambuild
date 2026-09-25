#!/bin/bash
# End-to-end test of deploy/slurm against the compose PostgreSQL and SeaweedFS
set -euo pipefail
export AMBUILD_RUNS_ROOT=/shared/runs
export AMBUILD_PARAMS_DIR=/ambuild/tests/params AMBUILD_BLOCKS_DIR=/ambuild/tests/blocks
export POREBLAZER_EXE=/opt/poreblazer/poreblazer.exe
export AMBUILD_UPLOAD_ENV=/root/upload.env
env | grep -E '^(DATABASE_URL|AMBUILD_S3_BUCKET|S3_ENDPOINT_URL|AWS_)' > "$AMBUILD_UPLOAD_ENV"
chmod 600 "$AMBUILD_UPLOAD_ENV"
slurm=/opt/ambuild-slurm
cd /tmp

run_id_of() { basename "$(tail -n 1 <<< "$1")"; }

ok=$("$slurm/submit_build.sh" --poreblazer "$slurm/example_build.py")
echo "$ok"
failed=$("$slurm/submit_build.sh" "$slurm/test/failing_build.py")
echo "$failed"
cancelled=$("$slurm/submit_build.sh" "$slurm/test/hanging_build.py")
echo "$cancelled"
multitask=$("$slurm/submit_build.sh" "$slurm/test/launcher_build.py" --ntasks=2)
echo "$multitask"
cancelled_dir=$(tail -n 1 <<< "$cancelled")
for _ in $(seq 120); do [ -f "$cancelled_dir/run.json" ] && break; sleep 1; done
scancel "$(grep -o 'build job [0-9]*' <<< "$cancelled" | awk '{print $3}')"

for _ in $(seq 600); do
    [ -z "$(squeue -h)" ] && break
    if sinfo -h -o %T | grep -qE 'down|drain|fail|[*]'; then
        echo "Slurm node failed" >&2
        tail -n 20 /var/log/slurm/*.log >&2
        exit 1
    fi
    sleep 2
done
squeue
echo "--- job outputs"; tail -n 3 /tmp/*.out
python3 "$slurm/test/check_db.py" "$(run_id_of "$ok")" "$(run_id_of "$failed")" "$(run_id_of "$cancelled")" \
    "$(run_id_of "$multitask")"
