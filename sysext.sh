#!/bin/bash

set -xe

install_dir=~/src/kde/sysext/kapsule
sudo rm -rf "$install_dir"
mkdir -p "$install_dir"

kde-builder --no-src --build-when-unchanged kapsule
sudo pacstrap -c "$install_dir" incus

serve_dir=/tmp/kapsule
mkdir -p "$serve_dir"

sudo tar -cf ${serve_dir}/kapsule.tar -C "$install_dir" usr

python -m http.server --directory "$serve_dir" 8000 &
trap "kill $!" EXIT

ssh root@192.168.100.157 \
    "importctl pull-tar --class=sysext --verify=no --force http://192.168.100.1:8000/kapsule.tar && \
     systemd-sysext refresh"
