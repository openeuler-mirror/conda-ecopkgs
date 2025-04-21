import argparse
import click
import json
import os
import requests
import shutil
import sys
import subprocess
import yaml
from typing import List, Any, Union, Tuple, Optional

DEFAULT_WORKDIR = "/tmp/ecopkgs/verify/"

CONDA_IMAGE_REPO = "openeuler/conda"
CONDA_IMAGE_VERSION = "25.1.1"

REPOSITORY_REQUEST_URL = (
    "https://gitee.com/api/v5/repos/openeuler/conda-ecopkgs/pulls"
)


# transform openEuler version into specifical format
# e.g., 22.03-lts-sp3 -> oe2203sp3
def _transform_version_format(os_version: str):
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

        # file named support-versions.yml and path format is
        # packages/{name}/support-versions.yml
        for change_file in change_files:
            if (not change_file.endswith("support-versions.yml")
                    or len(change_file.split("/")) != 3):
                continue

            versions_file = os.path.join(work_dir, change_file)
            if not os.path.exists(versions_file):
                continue

            package_root = os.path.dirname(versions_file)
            verify_script = os.path.join(package_root, "verify.sh")

            if verify_packages(versions_file, verify_script):
                continue
            else:
                click.echo(click.style(
                    f"Failed to verify support versions: {versions_file},"
                    f"verify script: {verify_script}",
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


def verify_packages(versions_file: str, verify_script: str) -> bool:
    """
    Parse support-versions.yaml file and return the
    latest openEuler and app version.

    Args:
        versions_file: Path to support-versions.yaml file
        verify_script: Path to verify.sh file

    Returns:
        Tuple of (latest openEuler version, latest app version)
        or None if error
    """
    with open(versions_file, 'r') as f:
        data = yaml.safe_load(f)

    if not data or not isinstance(data, dict):
        click.echo(click.style(
            f"Invalid YAML format in {versions_file}",
            fg="red"
        ))
        return False

    for os_version in data.keys():
        for app_version in data[os_version]:
            if run_verify_command(verify_script, os_version, app_version):
                continue
            return False

    return True


def run_verify_command(verify_script: str, os_version, app_version: str) -> bool:
    """
    Find and execute verify.sh in the package directory

    Args:
        verify_script: The shell script used to verify package
        os_version: latest os version
        app_version: latest package version

    Returns:
        True if execution succeeded, False otherwise
    """
    if not os.path.isfile(verify_script):
        click.echo(click.style(
            f"Install script not found {verify_script}",
            fg="red"
        ))
        return False

    os_suffix = _transform_version_format(os_version)
    image_tag = f"{CONDA_IMAGE_VERSION}-{os_suffix}"

    docker_cmd = []
    try:
        docker_cmd = [
            "sudo",
            "docker",
            "run",
            "--rm",
            "--privileged",
            "-v",
            f"{verify_script}:{verify_script}",
            f"{CONDA_IMAGE_REPO}:{image_tag}",
            "bash",
            "-x",
            "--",
            verify_script,
            "-v",
            app_version
        ]

        click.secho("🚀 Running Docker command:", fg="blue")
        click.secho("    " + " ".join(docker_cmd), fg="cyan")

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
    url = f"{REPOSITORY_REQUEST_URL}/{pr_id}"

    response = _request(url=url)
    if response.status_code != 200:
        raise RuntimeError(f"Request failed with status code:",
                           response.status_code)

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

    clear_all(work_dir)