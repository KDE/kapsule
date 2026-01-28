#!/bin/bash

set -xe

kde-builder --no-src --build-when-unchanged kapsule

serve_dir=/tmp/kapsule/
mkdir -p "$serve_dir"

tar -cf ${serve_dir}/kapsule.tar -C ~/src/kde/sysext/kapsule .

python -m http.server --directory "$serve_dir" 8000 &
trap "kill $!" EXIT

ssh root@192.168.100.157 importctl pull-tar --class=sysext --verify=no --force http://192.168.100.1:8000/kapsule.tar
