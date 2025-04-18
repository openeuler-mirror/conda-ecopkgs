#!/bin/bash
set -e

yum install -y python3 python3-pip wget

# 安装docker
if [[ ! $(which docker) ]]; then
    curl -sL https://raw.githubusercontent.com/cnrancher/euler-packer/refs/heads/main/scripts/others/install-docker.sh | sudo -E bash -
fi

pip3 install click requests

sudo -E python3 update/container/app/update.py \
	  -pr ${prid} \
    -sr ${repo} \
    -su ${scodeurl} \
    -br ${branch}