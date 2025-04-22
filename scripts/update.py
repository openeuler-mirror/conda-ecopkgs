import argparse
import click
import json
import os
import platform
import requests
import shutil
import sys
import subprocess
import yaml
from typing import List, Any, Union, Tuple, Optional

DEFAULT_WORKDIR = "/tmp/ecopkgs/verify/"

CONDA_IMAGE_REPO = "openeuler/conda"
CONDA_IMAGE_VERSION = "25.1.1"

SUPPORTED_VERSIONS_FILE = "supported-versions.yml"
PACKAGE_FILE = "package.yml"

REPOSITORY_REQUEST_URL = (
    "https://gitee.com/api/v5/repos/openeuler/conda-ecopkgs/pulls"
)

VERIFY_SCRIPT_URL = (
    "https://gitee.com/openeuler/conda-ecopkgs/raw/master/scripts/verify.sh"
)

# transform openEuler version into specifical format
# e.g., 22.03-lts-sp3 -> oe2203sp3
def transform_version_format(os_version: str):
    lower_version = os_version.lower()
    # check if os_version has substring "-sp"
    if "-sp" in lower_version:
        # delete "lts" in os_version
        lower_version = lower_version.replace("lts", "")
    # delete all "." and "-"
    ret = lower_version.replace(".", "").replace("-", "")

    return f"oe{ret}"


def verify_updates(pr_id: int, work_dir: str) -> bool:
    """
    Verify package updates by processing changed
    supported-versions.yml files and running verify scripts.

    Args:
        pr_id: Pull Request ID to identify changed files
        work_dir: CI working directory path

    Returns:
        bool: True if package was successfully verified, False otherwise
    """
    try:
        change_files = get_change_files(pr_id)
        if not change_files:
            click.echo(click.style("No changed files found", fg="red"))
            return False

        click.echo(click.style(
            f"Changed files:\n{json.dumps(change_files, indent=2)}",
            fg="blue"
        ))

        if os.path.exists(work_dir):
            os.chdir(work_dir)
        else:
            click.echo(click.style(
                f"Working directory not found - {work_dir}",
                fg="red"
            ))
            return False

        # file named supported-versions.yml and path format is
        # packages/{name}/supported-versions.yml
        for change_file in change_files:
            if (not change_file.endswith(SUPPORTED_VERSIONS_FILE)
                    or len(change_file.split("/")) != 3):
                continue
            if verify_packages(work_dir, change_file):
                continue
            else:
                click.echo(click.style(
                    f"Failed to verify versions: {change_file}",
                    fg="red"
                ))
                return False
        return True
    except Exception as e:
        click.echo(click.style(
            f"Unexpected error: {str(e)}",
            fg="red"
        ))
        return False


def verify_packages(work_dir: str, change_file: str) -> bool:
    """
    Parse supported-versions.yml file and return the
    latest openEuler and app version.

    Args:
        work_dir: CI working directory path
        change_file: Path to supported-versions.yml file

    Returns:
        Tuple of (latest openEuler version, latest app version)
        or None if error
    """
    versions_file = os.path.join(work_dir, change_file)
    if not os.path.exists(versions_file):
        click.echo(click.style(
            f"Supported-versions file not found: {versions_file}",
            fg="red"
        ))
        return False

    with open(versions_file, 'r') as f:
        data = yaml.safe_load(f)

    if not data or not isinstance(data, dict):
        click.echo(click.style(
            f"Invalid YAML format in {versions_file}",
            fg="red"
        ))
        return False

    package = change_file.split("/")[1]
    machine_arch = platform.machine()
    for os_version in data.keys():
        supported_arches = data[os_version]
        if not supported_arches:
            continue
        if machine_arch not in supported_arches:
            continue
        package_version = supported_arches[machine_arch]
        if not package_version:
            continue
        if run_verify_command(work_dir, package, os_version, package_version):
            continue
        return False

    return True


def parse_package_info(work_dir: str, package: str) -> Tuple[str, str]:
    package_info_file = f"{work_dir}/packages/{package}/{PACKAGE_FILE}"
    try:
        with open(package_info_file, 'r') as f:
            data = yaml.safe_load(f)
            return data.get("channel"), data.get("dependency-channels")
    except Exception as e:
        click.echo(click.style(
            f"Error parsing {package_info_file}",
            fg="red"
        ))
        raise e


def run_verify_command(work_dir, package, os_version, package_version: str) -> bool:
    """
    Find and execute verify.sh in the package directory

    Args:
        work_dir: CI working directory path
        package: conda package directory
        os_version: os version
        package_version: package version

    Returns:
        True if execution succeeded, False otherwise
    """
    verify_script = os.path.join(work_dir, "verify.sh")

    if not os.path.isfile(verify_script):
        click.echo(click.style(
            f"Install script not found {verify_script}",
            fg="red"
        ))
        return False

    channel, dependencies = parse_package_info(work_dir, package)

    os_suffix = transform_version_format(os_version)
    image_tag = f"{CONDA_IMAGE_VERSION}-{os_suffix}"

    docker_cmd = []
    try:
        docker_cmd = ["sudo", "docker", "run", "--rm", "--privileged",
                      "-v", f"{verify_script}:{verify_script}",
                      f"{CONDA_IMAGE_REPO}:{image_tag}",
                      "bash", "-x", "--", verify_script,
                      "-p", package,
                      "-c", channel,
                      "-v", package_version
                      ]

        if dependencies:
            docker_cmd.append("-d")
            docker_cmd.extend(dependencies)

        click.secho("Running docker command:", fg="blue")
        click.secho(" ".join(docker_cmd), fg="cyan")

        result = subprocess.run(
            docker_cmd,
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding='utf-8'
        )

        click.echo(click.style(
            f"Successfully executed verify.sh\nOutput: {result.stdout}",
            fg="green"
        ))
        return True

    except subprocess.CalledProcessError as e:
        click.echo(click.style(
            f"Install script failed (exit {e.returncode})\n"
            f"Command: {' '.join(docker_cmd)}\n"
            f"Error output: {e.stderr}",
            fg="red"
        ))
        return False

    except FileNotFoundError:
        click.echo(click.style(
            "Docker command not found. Is Docker installed and in PATH?",
            fg="red"
        ))
        return False

    except Exception as e:
        click.echo(click.style(
            f"Unexpected error: {str(e)}",
            fg="red"
        ))
        return False


def pull_source_code(pr_id, source_branch, work_dir):
    source_code_url = get_source_code(pr_id=pr_id)
    command = ['git', 'clone', '-b',
               source_branch,
               source_code_url,
               work_dir
               ]
    if subprocess.call(command) != 0:
        click.echo(click.style(
            f"Failed to clone {source_code_url}",
            fg="red"
        ))
        return 1
    return 0


def _request(url: str):
    cnt = 0
    response = None
    while (not response) and (cnt < 20):
        response = requests.get(url=url)
        cnt += 1
    return response


def get_change_files(pr_id) -> List[str]:
    change_files = []
    url = f"{REPOSITORY_REQUEST_URL}/{pr_id}/files?access_token=" + \
          os.environ["GITEE_API_TOKEN"]
    response = _request(url=url)
    # check status code
    if response.status_code == 200:
        files = response.json()
        for file in files:
            change_files.append(file['filename'])
    else:
        click.echo(click.style(
            f"Failed to fetch files: {response.status_code}",
            fg="red"
        ))
        return []
    return change_files


def get_source_code(pr_id) -> str:
    url = f"{REPOSITORY_REQUEST_URL}/{pr_id}?access_token=" + \
          os.environ["GITEE_API_TOKEN"]

    response = _request(url=url)
    if response.status_code != 200:
        raise RuntimeError(f"Request failed with status code:",
                           f"{response.status_code}, url: {url}")

    # Get the user repository info
    pr_data = response.json()
    head_repo = pr_data.get("head", {}).get("repo", {})
    if not head_repo:
        raise RuntimeError(f"User repo info not found,"
                           f"pull request: {url}.")

    # Get the source code url
    source_code_url = head_repo.get("html_url")
    if not source_code_url:
        raise RuntimeError(f"Source code url not found,"
                           f"pull request: {url}.")
    return source_code_url


def download_verify_script(work_dir: str) -> int:
    url = f"{VERIFY_SCRIPT_URL}?access_token=" + \
          os.environ["GITEE_API_TOKEN"]

    save_path = os.path.join(work_dir, "verify.sh")

    response = _request(url)
    if response.status_code == 200:
        with open(save_path, "wb") as f:
            f.write(response.content)
        return 0
    click.echo(click.style(
        f"Failed to download verify script,"
        f"status code: {response.status_code}, url: {url}",
        fg="red"
    ))
    return 1


def init_parser():
    new_parser = argparse.ArgumentParser(
        prog="update.py",
        description="update application container images",
    )

    new_parser.add_argument("-pr", "--prid", help="Pull Request ID")
    new_parser.add_argument(
        "-sr", "--source_repo", help="source repo of the PR"
    )
    new_parser.add_argument(
        "-br", "--source_branch", help="source branch of the PR"
    )
    return new_parser


def clear_all(work_dir: str):
    if not os.path.exists(work_dir):
        return
    shutil.rmtree(work_dir)


if __name__ == "__main__":
    parser = init_parser()
    args = parser.parse_args()

    if (
            not args.prid
            or not args.source_repo
            or not args.source_branch
    ):
        parser.print_help()
        sys.exit(1)

    # create workdir
    work_dir = os.path.join(DEFAULT_WORKDIR, args.source_repo)
    if os.path.exists(work_dir):
        shutil.rmtree(work_dir)
    os.makedirs(work_dir)

    if pull_source_code(
            args.prid,
            args.source_branch,
            work_dir
    ):
        sys.exit(1)

    if download_verify_script(work_dir):
        sys.exit(1)

    if not verify_updates(args.prid, work_dir):
        sys.exit(1)

    clear_all(work_dir)