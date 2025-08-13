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
from typing import List, Any, Tuple, Dict

DEFAULT_WORKDIR = "/tmp/ecopkgs/verify/"

CONDA_IMAGE_REPO = "openeuler/conda"
CONDA_IMAGE_VERSION = "25.1.1"

SUPPORTED_VERSIONS_FILE = "supported-versions.yml"
PACKAGE_FILE = "package.yml"
VERIFY_SCRIPT_FILE = "scripts/verify.sh"

UPDATE_CODE_DIR = "update"
ORIGIN_CODE_DIR = "origin"

REPOSITORY_REQUEST_URL = (
    "https://gitee.com/api/v5/repos/openeuler/conda-ecopkgs/pulls"
)
ORIGIN_CODE_URL = (
    "https://gitee.com/openeuler/conda-ecopkgs.git"
)


class NoFloatLoader(yaml.SafeLoader):
    pass


# Remove recognition of float
for ch in list(NoFloatLoader.yaml_implicit_resolvers):
    resolvers = [
        (tag, regexp)
        for tag, regexp in NoFloatLoader.yaml_implicit_resolvers[ch]
        if tag != 'tag:yaml.org,2002:float'
    ]
    if resolvers:
        NoFloatLoader.yaml_implicit_resolvers[ch] = resolvers
    else:
        del NoFloatLoader.yaml_implicit_resolvers[ch]


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

            if verify_change_file(work_dir, change_file):
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


def verify_change_file(work_dir: str, change_file: str) -> bool:
    """
    Verify the difference between updated and
    original supported-versions.yml.

    Args:
        work_dir: CI working directory path.
        change_file: Path to supported-versions.yml.

    Returns:
        True if all new entries are successfully verified,
        False otherwise.
    """
    # Load YAML data
    update_file = os.path.join(
        work_dir, UPDATE_CODE_DIR, change_file
    )
    origin_file = os.path.join(
        work_dir, ORIGIN_CODE_DIR, change_file
    )
    update_data = parse_yaml_data(update_file)
    origin_data = parse_yaml_data(origin_file)

    # only new os/version/arch need be verified
    package = change_file.split("/")[1]
    for os_version, versions in update_data.items():
        for package_version, arches in versions.items():
            for arch in arches:
                if not need_verify(
                        origin_data, os_version,
                        package_version, arch
                ):
                    continue
                if not verify_package(
                        work_dir, package,
                        os_version, package_version
                ):
                    return False
    return True


def need_verify(origin_data: dict, os_version: str,
                package_version: str, os_arch: str) -> bool:
    """
    Check whether a specific OS arch needs verification.

    Args:
        origin_data: Original YAML content.
        os_version: openEuler version string.
        package_version: Package version string.
        os_arch: Architecture to check.

    Returns:
        True if this arch is new and needs verification, False otherwise.
    """
    app_versions = origin_data.get(os_version, {})
    origin_arches = app_versions.get(package_version, [])

    # current machine arch or noarch arch need verified
    machine_arch = platform.machine()
    if os_arch != machine_arch and os_arch != "noarch":
        return False

    # Only verify if the arch is not present in the original list
    return os_arch not in origin_arches


def parse_yaml_data(yaml_file: str) -> Dict[str, Any]:
    if not os.path.exists(yaml_file):
        click.echo(click.style(
            f"File not found: {yaml_file}",
            fg="blue"
        ))
        return {}

    with open(yaml_file, 'r') as f:
        data = yaml.load(f, Loader=NoFloatLoader)
    return data


def parse_package_info(work_dir: str, package: str) -> Tuple[str, str]:
    package_info_file = os.path.join(
        work_dir,
        UPDATE_CODE_DIR,
        "packages",
        package,
        PACKAGE_FILE
    )
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


def verify_package(
        work_dir, package, os_version, package_version
) -> bool:
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
    verify_script = os.path.join(
        work_dir, ORIGIN_CODE_DIR, VERIFY_SCRIPT_FILE
    )

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
    os.makedirs(f"{work_dir}/{UPDATE_CODE_DIR}", exist_ok=True)
    source_code_url = get_source_code(pr_id=pr_id)
    command = ['git', 'clone', '-b',
               source_branch,
               source_code_url,
               f"{work_dir}/{UPDATE_CODE_DIR}"
               ]
    if subprocess.call(command) != 0:
        click.echo(click.style(
            f"Failed to clone {source_code_url}",
            fg="red"
        ))
        return 1
    return 0


def pull_origin_code(work_dir):
    os.makedirs(f"{work_dir}/{ORIGIN_CODE_DIR}", exist_ok=True)
    command = ['git', 'clone', ORIGIN_CODE_URL,
               f"{work_dir}/{ORIGIN_CODE_DIR}"
               ]
    if subprocess.call(command) != 0:
        click.echo(click.style(
            f"Failed to clone {ORIGIN_CODE_URL}",
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

    if pull_source_code(
            args.prid,
            args.source_branch,
            work_dir
    ):
        sys.exit(1)

    if pull_origin_code(work_dir):
        sys.exit(1)

    if not verify_updates(args.prid, work_dir):
        sys.exit(1)

    clear_all(work_dir)