#!/bin/bash
set -ex

yum install -y python3 python3-pip wget

# 安装docker
if [[ ! $(which docker) ]]; then
    curl -sL https://raw.githubusercontent.com/cnrancher/euler-packer/refs/heads/main/scripts/others/install-docker.sh | sudo -E bash -
    sudo rm -f /var/run/docker.sock
    sudo rm -rf /var/lib/docker/network/files
    sudo systemctl restart docker
fi

# clear unused resources
echo "清理缓存..."
docker image prune -f
docker container prune -f
docker network prune -f
docker volume prune -f
docker system prune -af
docker system df
echo "清理完成!"

rm -rf conda-ecopkgs
git clone https://gitee.com/openeuler/conda-ecopkgs.git
cd conda-ecopkgs

pip3 install click requests

sudo -E python3 scripts/update.py \
    -pr ${prid} \
    -sr ${repo} \
    -br ${branch}

rm -rf conda-ecopkgs