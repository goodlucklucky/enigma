# Docker GPU Test

A minimal proof-of-concept that confirms a Docker container can access and use
the host machine's NVIDIA GPU via CUDA.

The test (`minimal_test/`) builds a small CUDA container that:

1. Runs `nvidia-smi` inside the container.
2. Checks `numba.cuda.is_available()`.
3. Launches a tiny vector-add kernel on GPU 0 and verifies the result.

---

## Requirements

- An NVIDIA GPU.
- A Linux host (steps below target Ubuntu 22.04 / 24.04).
- Root / `sudo` access for the one-time setup.

---

## Setting up a new environment

These are the complete steps to take a fresh machine to the point where it can
build and run the GPU container. Steps 1-4 are one-time host setup and require
`sudo`. Step 5 onward needs no `sudo` once your user is in the `docker` group.

### 1. Install the NVIDIA driver

```bash
sudo apt update
sudo apt install -y ubuntu-drivers-common
ubuntu-drivers devices                          # see the recommended driver
sudo apt install -y nvidia-driver-580-server-open   # or the recommended version (sudo ubuntu-drivers autoinstall)
sudo reboot
```

After reboot, confirm the driver works:

```bash
nvidia-smi
```

You should see your GPU and a driver version.

### 2. Install Docker

```bash
curl -fsSL https://get.docker.com | sudo sh
sudo systemctl enable --now docker
```

### 3. Install the NVIDIA Container Toolkit

This is what allows containers to use the host GPU when you pass `--gpus`.

```bash
curl -fsSL https://nvidia.github.io/libnvidia-container/gpgkey | \
  sudo gpg --dearmor -o /usr/share/keyrings/nvidia-container-toolkit-keyring.gpg

curl -s -L https://nvidia.github.io/libnvidia-container/stable/deb/nvidia-container-toolkit.list | \
  sed 's#deb https://#deb [signed-by=/usr/share/keyrings/nvidia-container-toolkit-keyring.gpg] https://#g' | \
  sudo tee /etc/apt/sources.list.d/nvidia-container-toolkit.list

sudo apt update
sudo apt install -y nvidia-container-toolkit
```

### 4. Wire the toolkit into Docker

```bash
sudo nvidia-ctk runtime configure --runtime=docker
sudo systemctl restart docker
```

### 5. Allow your user to run Docker without sudo

```bash
sudo usermod -aG docker $USER
newgrp docker          # or log out and back in
```

> **Note:** Membership in the `docker` group is effectively root-equivalent.
> This is the standard trade-off for running Docker without `sudo`.

### 6. Verify GPU passthrough

```bash
docker run --rm --gpus all nvidia/cuda:12.6.0-base-ubuntu22.04 nvidia-smi
```

If this prints the GPU table, the host is fully configured.

---

## Build and run the test

```bash
cd minimal_test
docker build -t cuda-gpu-poc .
docker run --rm --gpus all cuda-gpu-poc
```

### Select a specific GPU

```bash
docker run --rm --gpus device=0 cuda-gpu-poc
```

---

## Expected output

```
=== nvidia-smi ===
(... GPU table ...)

=== CUDA runtime (Numba) ===
GPU count: 1
Device 0: NVIDIA ... (compute X.Y)
Vector add on GPU OK: [11.0, 22.0, 33.0, 44.0]

SUCCESS: GPU is accessible from this container.
```

---

## Important: image variant for the Numba kernel

The `runtime` CUDA image lets `nvidia-smi` work but is missing the CUDA
compiler libraries (`libnvvm.so`) that Numba needs to JIT a kernel, so
`cuda.is_available()` returns `False`.

For the full kernel test to pass, `minimal_test/Dockerfile` must use the
`devel` base image:

```dockerfile
FROM nvidia/cuda:12.6.0-devel-ubuntu22.04
```

If you only need to prove the container can see the GPU (not compile a kernel),
the `runtime` image plus `nvidia-smi` is sufficient.

---

## Troubleshooting

| Symptom | Likely cause | Fix |
|---------|--------------|-----|
| `nvidia-smi` fails on host | Driver not installed / needs reboot | Reinstall driver, reboot (step 1) |
| `docker: Error response ... could not select device driver` | Toolkit not wired into Docker | Run step 4, restart Docker |
| `permission denied` on `docker.sock` | User not in `docker` group | Run step 5, start a new login session |
| `nvidia-smi` works in container but `cuda.is_available()` is `False` | `runtime` image lacks `libnvvm.so` | Use the `devel` base image |

### Quick diagnostic checklist

```bash
nvidia-smi                                  # host driver
systemctl is-active docker                  # daemon running
dpkg -l | grep nvidia-container-toolkit     # toolkit installed
docker run --rm --gpus all \
  nvidia/cuda:12.6.0-base-ubuntu22.04 nvidia-smi -L   # passthrough works
```

If all four pass, Docker + NVIDIA is configured correctly and any remaining
failure is in the container image or application.
