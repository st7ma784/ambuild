# Installing Docker and GPU support on a Linux host

Step-by-step host setup for running Ambuild's Docker images, moved here from the README
(which covers installing Ambuild itself). The instructions were written for
Ubuntu/Debian; copy each grey box in turn into a terminal. Skip the steps for anything
you already have.

## 1. Install Docker

Instructions from: https://docs.docker.com/engine/install/ubuntu/

1. Firstly, remove any old versions with the command:
  ```
sudo apt-get remove docker docker-engine docker.io containerd runc
```

Update the list of packages and install those required to install Docker with the following two commands:
  ```
sudo apt-get update
  ```
  ```
sudo apt-get install \
    apt-transport-https \
    ca-certificates \
    curl \
    gnupg-agent \
    software-properties-common
```

2. Add Docker’s official GPG key (so that the downloaded packages can be validated) with the command:
```
curl -fsSL https://download.docker.com/linux/ubuntu/gpg | sudo apt-key add -
```
3. Add the Docker 'stable' repository to the list of available repositories, so that apt can download from it:
```
sudo add-apt-repository \
   "deb [arch=amd64] https://download.docker.com/linux/ubuntu \
   $(lsb_release -cs) \
   stable"
```
4. With the Docker repository added to the list, update the list of packages, and then install docker:
```
sudo apt-get update
  ```
  ```
sudo apt-get install docker-ce docker-ce-cli containerd.io
```

## 2. Create a docker group
Non-root users cannot run Docker by default, so it usually needs to be run under sudo; however this means any files created are owned by root, which is not a good idea. To allow users to run docker/ambuild without having sudo access, create docker group and add any users to it who will be required to run docker/ambuild.

1. First create the group for all users of docker. This may have already been done with the docker installation command, so it may not be required, but it's not a problem to run this command again.
```
sudo groupadd docker
```

2. Add the current logged in user (specified by the $USER environment variable) to this group. Any other users can be added by replacing $USER in the below command with the Unix username.
```
sudo usermod -aG docker $USER
````
3. Activate the group (this just saves logging out/in again)
```
newgrp docker
```
4. With Docker installed, the docker group set up and the current user added, test if you can run docker without using sudo:
```
docker run hello-world
```
If you see the following output when running the line above, you have a working Docker installation! If you do not see the output below please contact your local Linux specialist or visit the [Docker website](https://docs.docker.com/).

```
Hello from Docker!
This message shows that your installation appears to be working correctly.

To generate this message, Docker took the following steps:
1. The Docker client contacted the Docker daemon.
2. The Docker daemon pulled the "hello-world" image from the Docker Hub. (amd64)
3. The Docker daemon created a new container from that image which runs the executable that produces the output you are currently reading.
4. The Docker daemon streamed that output to the Docker client, which sent it to your terminal.
To try something more ambitious, you can run an Ubuntu container with: $ docker run -it ubuntu bash
Share images, automate workflows, and more with a free Docker ID: https://hub.docker.com/
For more examples and ideas, visit: https://docs.docker.com/get-started/
```

## 3. Install the NVIDIA drivers (GPU image only)
In order for applications within the Docker container to take advantage of GPU acceleration, you will need to install the NVIDIA GPU drivers for your card - the drivers are the piece of software that allow different programmes to communicate with the GPU card. There are instructions for how to do this on the [NVIDIA website](https://docs.nvidia.com/cuda/cuda-installation-guide-linux/index.html)

On Ubuntu, the easiest way to do this seems to be with the command:
```
sudo ubuntu-drivers autoinstall
```

## 4. Install the NVIDIA container runtime (GPU image only)
Instructions from: https://github.com/NVIDIA/nvidia-docker

1. Run the following commands to install the nvidia-container-toolkit:

  ```
distribution=$(. /etc/os-release;echo $ID$VERSION_ID)
  ```
  ```
curl -s -L https://nvidia.github.io/nvidia-docker/gpgkey | sudo apt-key add -
  ```
  ```
curl -s -L https://nvidia.github.io/nvidia-docker/$distribution/nvidia-docker.list | sudo tee /etc/apt/sources.list.d/nvidia-docker.list
  ```
  ```
sudo apt-get update && sudo apt-get install -y nvidia-container-toolkit
  ```
  ```
sudo systemctl restart docker
  ```

2. Test nvidia-smi with the latest official CUDA image

  ```docker run --gpus all nvidia/cuda:10.0-base nvidia-smi```

3. Install the nvidia-container-runtime
```
sudo apt install nvidia-container-runtime
```
There is currently a bug with the nvidia docker container runtime as detailed here: https://github.com/docker/compose/issues/6691

To work around the bug, carry out the following additional step:

4. Create a file called /etc/docker/daemon.json with the following content by typing the following (cut and paste the following command from 'sudo' to the second 'EOF' into the terminal):

```
sudo tee -a /etc/docker/daemon.json << EOF
{
    "runtimes": {
        "nvidia": {
            "path": "/usr/bin/nvidia-container-runtime",
            "runtimeArgs": []
        }
    }
}
EOF
```
5. Restart docker:
```
sudo systemctl restart docker
```

## 5. Keep jobs off a display GPU (optional)
If you have more than one GPU card (e.g. you have a card specifically for running jobs), then you may need to disable your video GPU card for running jobs so that any GPU jobs are placed on the specialised card rather than using the video card. This will not disable the video card for viewing your screen - it will just prevent it being used to run computational simulation jobs.

1. Find ID of card to disable (this will print the UUID string as is seen in the example below, that you can then use in step 2).
```
nvidia-smi -L
```
An example output of ```nvidia-smi -L``` is given here:
```
GPU 0: Tesla K40c (UUID: GPU-66dc2593-494d-4b44-4574-2b92976db56b)
```

2. Disable video GPU by setting mode to 2/PROHIBITED for the GPU card who's UUID we identified with the command above. 
```
sudo nvidia-smi -c 2 -i GPU-4030396e-e7b4-aa4d-e035-22758536dba5
```
E.g. in the example output above, the UUID string will be: ```GPU-66dc2593-494d-4b44-4574-2b92976db56b```, making the command in step 2 read:  ```sudo nvidia-smi -c 2 -i GPU-66dc2593-494d-4b44-4574-2b92976db56b```

If you do not see an output like the one given as an example in step 1, we advise you to firstly try running the three commands immediately below. If you still do not see an output as in the example given with step 1, we suggest contacting your local Linux specialist.

```
sudo apt-get --purge remove "*cublas*" "cuda" "nsight"
```
```
sudo apt-get --purge remove "*nvidia*"
```
```
sudo apt-get autoremove
```
```
sudo ubuntu-drivers autoinstall
```

