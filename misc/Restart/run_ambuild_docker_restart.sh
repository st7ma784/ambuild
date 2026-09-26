  GNU nano 4.8                                                                           run_ambuild_docker_restart.sh                                                                                     
#!/bin/bash

# Usage:
# ambuild_docker.py <volume_arguments> script

# Get root dir and script argumnts
run_dir="$PWD"
ambuild_dir="$( cd "$( dirname "${BASH_SOURCE[0]}" )/.." >/dev/null 2>&1 && pwd )"
script=$1
extra_args=""
if [ ${#} -ge 2 ]; then
   script="${@:(-1):1}"
   extra_args="${@:1:$(($#-1))}"
fi

# Run as the calling user so files written to the mounts are theirs; with rootless Docker
# the container's root user already is the calling user.
user_args=(--user "$(id -u):$(id -g)")
if docker info --format '{{.SecurityOptions}}' 2>/dev/null | grep -q rootless; then
    user_args=()
fi
MAXLOOP=100

LOOPCOUNT=0
RESTART=1
while [ $RESTART -eq 1 ] && [ $LOOPCOUNT -lt $MAXLOOP ]; do
  echo $MAXLOOP
  echo Restart file is:
  ls -1t step_*.pkl.gz|head -1
  RFILE=`ls -1t step_*.pkl.gz|head -1`
  #STDIR=${> restart.o.$LOOPCOUNT 2> restart.e.$LOOPCOUNT}
  STDIR=" > restart.o.${LOOPCOUNT} 2> restart.e.${LOOPCOUNT}"
  FULLSTRING="${script} -i ${RFILE}"
  # Run in the HOOMD-blue 7 image: AMBUILD_IMAGE chooses it (default ambuild-hoomd7, see
  # tests/docker/hoomd7.Dockerfile); add --gpus all to the arguments for the GPU build
  docker run \
  --rm \
  "${user_args[@]}" \
  --volume $run_dir:$run_dir \
  --volume ${ambuild_dir}/ambuild:/opt/ambuild/ambuild:ro \
  --env PYTHONPATH=/opt/ambuild \
  --workdir $run_dir \
  $extra_args \
  "${AMBUILD_IMAGE:-ambuild-hoomd7}" \
  python $FULLSTRING > restart.o.${LOOPCOUNT} 2> restart.e.${LOOPCOUNT}
  mv runmd.log runmd.log.$LOOPCOUNT
  ./testrestart restart.e.$LOOPCOUNT
  RESTART=$?
  LOOPCOUNT=$[$LOOPCOUNT+1]
done

if [ $LOOPCOUNT -eq $MAXLOOP ]; then
   echo Error: Maximum restart loop count value of $MAXLOOP reached
fi


