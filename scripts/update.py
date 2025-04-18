import argparse
import click
import json
import os
import re
import requests
import shutil
import sys
import subprocess
import yaml
from typing import List, Any, Union

DEFAULT_WORKDIR = "/tmp/ecopkgs/verify/"

CONDA_IMAGE_REPO = "baibgj/conda"
CONDA_IMAGE_TAG = "latest"

REPOSITORY_REQUEST_URL = (
    "https://gitee.com/api/v5/repos/openeuler/conda-ecopkgs/pulls"
)


def convert_to_number(os_version: str) -> Union[float, None]:
    """
    Convert openEuler version string to a comparable float value for easy ordering.

    Processing steps:
    1. Remove 'openEuler-' prefix if present
    2. Remove '-LTS' suffix if present
    3. Remove '-SP' suffixes (e.g., '-SP1' becomes '1')
    4. Remove all non-digit characters except dots

    Examples:
        "24.03-LTS" → 24.03
        "24.03-LTS-SP1" → 24.031
        "24.03" → 24.03
        "24.03-LTS-SP2" → 24.032
        "openEuler-24.03-LTS-SP2" → 24.032

    Args:
        os_version: The openEuler version string to convert

    Returns:
        Float representation of the version suitable for comparison
        Returns none if conversion fails (invalid format)
    """
    version = (
        os_version.replace("openEuler-", "")
        .replace("-LTS", "")
        .replace("-SP", "")
    )
    version = re.sub(r"[^\d.]", "", version)
    try:
        return float(version)
    except ValueError:
        click.echo(click.style(
            f"Invalid version format {version}",
            fg="red"
        ))
        return None


def get_latest_version(versions_file: str) -> Union[Any, None]:
    """
    Parse versions_file.yml file and find latest openEuler and package versions

    Args:
        versions_file: Path to supprot-versions.yaml file

    Returns:
        Latest app version or None if not found
    """
    try:
        with open(versions_file, 'r') as f:
            data = yaml.safe_load(f)

        if not data or not isinstance(data, dict):
            click.echo(click.style(
                f"Invalid YAML format in {versions_file}",
                fg="red"
            ))
            return None

        # keep string version and float version
        os_map = {}
        for os_version in data.keys():
            version_num = convert_to_number(os_version)
            if not version_num:
                return None
            os_map[os_version] = version_num

        if not os_map:
            click.echo(click.style(
                f"No valid openEuler versions found in {versions_file}",
                fg="red"
            ))
            return None

        max_os = max(os_map.keys(), key=lambda x: os_map[x])
        app_versions = data.get(max_os, [])
        if not app_versions:
            click.echo(click.style(
                f"Error: No app versions found for {max_os}",
                fg="red"
            ))
            return None

        return app_versions[-1]
    except Exception as e:
        click.echo(click.style(
            f"Unexpected error processing {versions_file}: {e}",
            fg="red"
        ))
        return None


def verify_package(verify_script: str, latest_version: str) -> bool:
    """
    Find and execute verify.sh in the package directory

    Args:
        verify_script: The shell script used to verify package
        latest_version: latest conda package version

    Returns:
        True if execution succeeded, False otherwise
    """
    docker_cmd = []
    if not os.path.isfile(verify_script):
        click.echo(click.style(
            f"Install script not found {verify_script}",
            fg="red"
        ))
        return False

    try:
        docker_cmd = [
            "sudo",
            "docker",
            "run",
            "--privileged",
            "-v",
            f"{verify_script}:{verify_script}",
            "--rm",
            f"{CONDA_IMAGE_REPO}:{CONDA_IMAGE_TAG}",
            "bash",
            "-x",
            "--",
            verify_script,
            "-v",
            latest_version
        ]

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


def verify_updates(pr_id: int, work_dir: str) -> bool:
    """
    Verify package updates by processing changed
    support_versions.yml files and running verify scripts.

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

        for change_file in change_files:
            if not change_file.endswith("support-versions.yml"):
                continue

            versions_file = os.path.join(work_dir, change_file)

            # file deleted
            if not os.path.exists(versions_file):
                continue

            package_root = os.path.dirname(versions_file)
            verify_script = os.path.join(package_root, "verify.sh")

            latest_version = get_latest_version(versions_file)
            if not latest_version:
                click.echo(click.style(
                    f"Failed to get version from {versions_file}",
                    fg="red"
                ))
                return False

            if not verify_package(verify_script, latest_version):
                click.echo(click.style(
                    f"Failed to verify version "
                    f"{latest_version} from {versions_file}",
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
    url = f"{REPOSITORY_REQUEST_URL}/{pr_id}"

    response = _request(url=url)
    if response.status_code != 200:
        raise RuntimeError(
            f"Request failed with status code:",
            response.status_code
        )

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

    if not verify_updates(args.prid, work_dir):
        sys.exit(1)
