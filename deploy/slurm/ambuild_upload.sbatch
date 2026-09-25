#!/bin/bash
# Upload a run directory and any child runs below it with ambuild-upload.
# Submitted with --dependency=afterany:<job> so it runs whether the job it
# follows finished, failed, timed out or was cancelled; --finalise records
# a run whose process died without closing it as "incomplete".
#
# Credentials (DATABASE_URL, AMBUILD_S3_BUCKET, S3_ENDPOINT_URL,
# AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY) are read from AMBUILD_UPLOAD_ENV,
# default ~/.config/ambuild/upload.env; keep that file mode 600.
#SBATCH --job-name=ambuild-upload
#SBATCH --ntasks=1
#SBATCH --time=00:30:00
#SBATCH --output=%x-%j.out
set -euo pipefail
: "${AMBUILD_RUN_DIR:?}"

env_file="${AMBUILD_UPLOAD_ENV:-$HOME/.config/ambuild/upload.env}"
set -a
# shellcheck disable=SC1090
source "$env_file"
set +a

ambuild-upload --finalise --recursive "$AMBUILD_RUN_DIR"
