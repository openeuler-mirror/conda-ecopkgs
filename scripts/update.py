import argparse
import click
import json
import os
import platform
import re
import requests
import shutil
import subprocess
import sys
import tempfile
import time
import yaml
from datetime import datetime, timezone
from typing import List, Any, Tuple, Dict

DEFAULT_WORKDIR = "/tmp/ecopkgs/verify/"

CONDA_IMAGE_REPO = "openeuler/conda"
CONDA_IMAGE_VERSION = "25.1.1"

SUPPORTED_VERSIONS_FILE = "supported-versions.yml"
PACKAGE_FILE = "package.yml"
VERIFY_SCRIPT_FILE = "scripts/verify.sh"
TEST_RUNNER_FILE = "scripts/test-runner.py"
CASES_FILE = "tests/cases.yml"
OS_VERSIONS_FILE = "config/os-versions.txt"

UPDATE_CODE_DIR = "update"
ORIGIN_CODE_DIR = "origin"

# CI 仓库与 PR API 可通过 CI_REPO_SLUG 切换（测试期指向个人 fork），缺省保持上游行为
_REPO_SLUG = os.environ.get("CI_REPO_SLUG", "openeuler/conda-ecopkgs")
REPOSITORY_REQUEST_URL = (
    f"https://api.gitcode.com/api/v5/repos/{_REPO_SLUG}/pulls"
)
ORIGIN_CODE_URL = (
    f"https://gitcode.com/{_REPO_SLUG}.git"
)

# 测试与报告仅在指定架构的节点上执行（CI 在 x86/arm 各有一个 job，测试只跑 ARM）
TEST_ARCH = os.environ.get("TEST_ARCH", "aarch64")

# 测试报告推送（upstream-dashboard 仓库 KunpengTest 分支，目录 openeuler-conda-test）
# 报告仓库为完整 URL（如 https://github.com/opensourceways/upstream-dashboard.git）
DASHBOARD_REPO_URL = os.environ.get("DASHBOARD_REPO_URL", "")
DASHBOARD_BRANCH = os.environ.get("DASHBOARD_BRANCH", "KunpengTest")
DASHBOARD_RESULTS_DIR = os.environ.get("DASHBOARD_RESULTS_DIR", "openeuler-conda-test")
DASHBOARD_COMMIT_NAME = os.environ.get("DASHBOARD_COMMIT_NAME", "conda-ecopkgs-ci")
DASHBOARD_COMMIT_EMAIL = os.environ.get("DASHBOARD_COMMIT_EMAIL", "ci@openeuler.org")
# 报告 schema 版本与安装方式（团队约定固定 0.6 + conda）
REPORT_SCHEMA_VERSION = os.environ.get("REPORT_SCHEMA_VERSION", "0.6")
REPORT_INSTALL_METHOD = os.environ.get("REPORT_INSTALL_METHOD", "conda")
DRY_RUN = os.environ.get("DRY_RUN", "") not in ("", "0", "false")


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


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def read_target_oses(base_dir: str) -> List[str]:
    """config/os-versions.txt 声明需要跑测试并产出报告的目标 OS（最新的 openEuler 版本）

    文件里允许带 openEuler- 前缀（如 openEuler-24.03-LTS-SP4），
    统一去掉前缀以对齐 supported-versions.yml 的 key。
    """
    path = os.path.join(base_dir, OS_VERSIONS_FILE)
    if not os.path.exists(path):
        return []
    target_oses = []
    with open(path, "r") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            if line.lower().startswith("openeuler-"):
                line = line[len("openeuler-"):]
            target_oses.append(line)
    return target_oses


def load_cases(tests_dir: str) -> List[Dict[str, Any]]:
    """解析 packages/{pkg}/tests/cases.yml，返回测试用例列表"""
    path = os.path.join(tests_dir, "cases.yml")
    if not os.path.exists(path):
        return []
    with open(path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    cases = []
    for c in data.get("cases", []):
        if not c.get("cmd"):
            continue
        cases.append({
            "name": str(c.get("name", "case")),
            "description": str(c.get("description") or c.get("name", "case")),
            "category": c.get("category", "functional"),
            "cmd": c["cmd"],
            "timeout": int(c.get("timeout", 120)),
            "skip": bool(c.get("skip", False)),
            "blocked": bool(c.get("blocked", False)),
        })
    return cases


def verify_updates(pr_id: int, work_dir: str) -> Tuple[bool, List[dict]]:
    """
    Verify package updates by processing changed
    supported-versions.yml files and running verify scripts.

    Returns:
        Tuple[bool, List[dict]]: overall success and structured results
    """
    results: List[dict] = []
    try:
        change_files = get_change_files(pr_id)
        if not change_files:
            click.echo(click.style("No changed files found", fg="red"))
            return False, results

        click.echo(click.style(
            f"Changed files:\n{json.dumps(change_files, indent=2)}",
            fg="blue"
        ))

        update_dir = os.path.join(work_dir, UPDATE_CODE_DIR)
        target_oses = read_target_oses(update_dir)
        if target_oses:
            click.echo(click.style(
                f"Test target OSes (tests + reports): {target_oses}",
                fg="blue"
            ))

        # file named supported-versions.yml and path format is
        # packages/{name}/supported-versions.yml
        for change_file in change_files:
            if (not change_file.endswith(SUPPORTED_VERSIONS_FILE)
                    or len(change_file.split("/")) != 3):
                continue

            verify_change_file(work_dir, change_file, target_oses, results)

        success = all(r["status"] == "passed" for r in results)
        if not success:
            for r in results:
                if r["status"] != "passed":
                    click.echo(click.style(
                        f"Failed to verify versions: {r['package']} "
                        f"{r['os_version']} {r['package_version']}",
                        fg="red"
                    ))
        return success, results
    except Exception as e:
        click.echo(click.style(
            f"Unexpected error: {str(e)}",
            fg="red"
        ))
        return False, results


def verify_change_file(work_dir: str, change_file: str,
                       target_oses: List[str], results: List[dict]) -> None:
    """
    Verify the difference between updated and original supported-versions.yml.

    Args:
        work_dir: CI working directory path.
        change_file: Path to supported-versions.yml.
        target_oses: OS versions that run tests and produce reports.
        results: collected structured results (appended in place).
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
                # 测试 + 报告只在该架构节点 + 目标 OS 上执行；其余仅安装验证
                with_tests = (platform.machine() == TEST_ARCH
                              and os_version in target_oses)
                entry = verify_package(
                        work_dir, package,
                        os_version, package_version,
                        with_tests
                )
                entry["tested"] = with_tests
                results.append(entry)


def need_verify(origin_data: dict, os_version: str,
                package_version: str, os_arch: str) -> bool:
    """
    Check whether a specific OS arch needs verification.
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


def parse_package_info(code_dir: str, package: str) -> Tuple[str, str]:
    package_info_file = os.path.join(
        code_dir,
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


def prepare_test_assets(update_dir: str, package: str, work_dir: str,
                        os_version: str, package_version: str) -> Tuple[str, str, str]:
    """
    从代码目录读取测试用例，生成容器内使用的 test spec。

    Returns:
        (spec_path, tests_dir, results_dir)
    """
    tests_dir = os.path.join(update_dir, "packages", package, "tests")
    cases = load_cases(tests_dir)
    if not cases:
        return "", "", ""

    runner_src = None
    for candidate in (update_dir, os.path.join(work_dir, ORIGIN_CODE_DIR)):
        path = os.path.join(candidate, TEST_RUNNER_FILE)
        if os.path.isfile(path):
            runner_src = path
            break
    if not runner_src:
        click.echo(click.style(
            f"Test runner not found: {TEST_RUNNER_FILE}",
            fg="red"
        ))
        return "", "", ""

    assets_dir = os.path.join(
        work_dir, "results", f"{package}-{package_version}-{os_version}"
    )
    os.makedirs(assets_dir, exist_ok=True)

    spec = {
        "env": {
            "PKG_VERSION": package_version,
            "OS_VERSION": os_version,
            "ARCH": platform.machine(),
        },
        "cases": cases,
    }
    spec_path = os.path.join(assets_dir, "test-spec.json")
    with open(spec_path, "w", encoding="utf-8") as f:
        json.dump(spec, f, ensure_ascii=False, indent=2)

    runner_dst = os.path.join(assets_dir, "test-runner.py")
    shutil.copyfile(runner_src, runner_dst)

    return spec_path, tests_dir, assets_dir


def verify_package(
        work_dir, package, os_version, package_version,
        with_tests=False, update_dir=None, origin_dir=None
) -> dict:
    """
    Find and execute verify.sh in the package directory.

    Returns structured result:
    {
        package, os_version, package_version, arch,
        install: "success"|"fail", tests: [...],
        tested: bool, test_time, finished_at, status: "passed"|"failed"
    }
    """
    entry = {
        "package": package,
        "os_version": os_version,
        "package_version": package_version,
        "arch": platform.machine(),
        "install": "fail",
        "tests": [],
        "tested": False,
        "test_time": now_iso(),
        "finished_at": None,
        "status": "failed",
    }

    update_dir = update_dir or os.path.join(work_dir, UPDATE_CODE_DIR)
    origin_dir = origin_dir or os.path.join(work_dir, ORIGIN_CODE_DIR)

    # verify.sh 优先取 PR head 的版本，保证与本次 update.py 的参数协议一致；
    # PR 未改动 verify.sh 时两者内容相同
    verify_script = None
    for candidate in (update_dir, origin_dir):
        path = os.path.join(candidate, VERIFY_SCRIPT_FILE)
        if os.path.isfile(path):
            verify_script = path
            break

    if not verify_script:
        click.echo(click.style(
            f"Install script not found {verify_script}",
            fg="red"
        ))
        entry["finished_at"] = now_iso()
        return entry

    channel, dependencies = parse_package_info(update_dir, package)

    os_suffix = transform_version_format(os_version)
    image_tag = f"{CONDA_IMAGE_VERSION}-{os_suffix}"

    spec_path, tests_dir, results_dir = "", "", ""
    if with_tests:
        spec_path, tests_dir, results_dir = prepare_test_assets(
            update_dir, package, work_dir, os_version, package_version
        )
        if not spec_path:
            # 无测试用例声明，退化为纯安装验证
            with_tests = False

    docker_cmd = [
        "sudo", "docker", "run", "--rm", "--privileged",
        "-v", f"{verify_script}:{verify_script}"
    ]
    args = ["-p", package, "-c", channel, "-v", package_version]

    if with_tests:
        runner_path = os.path.join(results_dir, "test-runner.py")
        docker_cmd += [
            "-v", f"{spec_path}:/tmp/test-spec.json:ro",
            "-v", f"{runner_path}:/tmp/test-runner.py:ro",
            "-v", f"{tests_dir}:/tests:ro",
            "-v", f"{results_dir}:/results",
        ]
        args += ["-t", "/tmp/test-spec.json", "-r", "/results"]

    docker_cmd += [
        f"{CONDA_IMAGE_REPO}:{image_tag}",
        "bash", "-x", "--", verify_script,
    ] + args

    if dependencies:
        docker_cmd.append("-d")
        docker_cmd.extend(dependencies)

    click.secho("Running docker command:", fg="blue")
    click.secho(" ".join(docker_cmd), fg="cyan")

    try:
        subprocess.run(
            docker_cmd,
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding='utf-8'
        )
        entry["install"] = "success"

    except subprocess.CalledProcessError as e:
        stderr = (e.stderr or "")[-4000:]
        click.echo(click.style(
            f"Install script failed (exit {e.returncode})\n"
            f"Command: {' '.join(docker_cmd)}\n"
            f"Error output: {stderr}",
            fg="red"
        ))

    except FileNotFoundError:
        click.echo(click.style(
            "Docker command not found. Is Docker installed and in PATH?",
            fg="red"
        ))

    except Exception as e:
        click.echo(click.style(
            f"Unexpected error: {str(e)}",
            fg="red"
        ))

    # 无论成功失败，尽量读取 runner 写出的用例结果（失败时也保留逐条详情）
    if with_tests:
        results_file = os.path.join(results_dir, "test-results.json")
        if os.path.exists(results_file):
            with open(results_file, "r", encoding="utf-8") as f:
                entry["tests"] = json.load(f)

    passed = entry["install"] == "success" and not any(
        t.get("result") in ("fail", "error") for t in entry["tests"]
    )
    entry["status"] = "passed" if passed else "failed"

    click.echo(click.style(
        f"Verification {'passed' if passed else 'failed'}: "
        f"{package} {os_version} {package_version}",
        fg="green" if passed else "red"
    ))

    entry["finished_at"] = now_iso()
    return entry


# ---------------------------------------------------------------------------
# KunpengTest 报告
# ---------------------------------------------------------------------------

def build_report(entry: dict, hardware: str,
                 pr_id=None, job_url=None) -> Dict[str, Any]:
    """将一次验证结果组装为 KunpengTest schema 0.6 报告"""
    passed = entry["status"] == "passed"

    report = {
        "schema_version": REPORT_SCHEMA_VERSION,
        "software": entry["package"],
        "version": entry["package_version"],
        "hardware": hardware,
        "os": transform_version_format(entry["os_version"]),
        "test_time": entry["test_time"],
        "finished_at": entry["finished_at"],
        "run_status": "completed",
        # 约定 CI 通过即为合入：通过则直接展示
        "display": passed,
        "CI_passed": passed,
        "PR_merged": passed,
        "upstream_supported": True,
        "unsupported_reasons": [],
        "test_cases": entry["tests"],
        "install_method": REPORT_INSTALL_METHOD,
        "extensions": {
            "arch": entry["arch"],
            "install_verified": entry["install"] == "success",
        },
    }
    if pr_id:
        report["prid"] = str(pr_id)
    if job_url:
        report["job_url"] = job_url
    return report


def report_relpath(report: dict, filename: str) -> str:
    """upstream-dashboard 目录约定: <报告根目录>/<A-Z>/<软件名>/<软件版本>/<文件名>"""
    software = report["software"]
    first = software[0].upper()
    shard = first if first.isalpha() else "0-9"
    return os.path.join(
        DASHBOARD_RESULTS_DIR, shard, software, report["version"], filename
    )


def make_report_file(entry: dict, hardware: str,
                     pr_id=None, job_url=None) -> Tuple[dict, str, str]:
    """生成报告 dict、文件名和相对路径"""
    os_short = transform_version_format(entry["os_version"])
    # test_time: 2026-11-24T08:15:30Z -> 20261124T081530Z
    ts = entry["test_time"].replace("-", "").replace(":", "")
    filename = f"{hardware}-{os_short}-{ts}.json"
    report = build_report(entry, hardware, pr_id, job_url)
    return report, filename, report_relpath(report, filename)


def write_reports(reports: List[Tuple[dict, str, str]], out_root: str) -> None:
    for report, filename, relpath in reports:
        full = os.path.join(out_root, relpath)
        os.makedirs(os.path.dirname(full), exist_ok=True)
        with open(full, "w", encoding="utf-8") as f:
            json.dump(report, f, ensure_ascii=False, indent=2)
        click.echo(click.style(f"Report written: {full}", fg="green"))


def _sanitize_repo(url: str) -> str:
    """日志中隐藏仓库 URL 里可能嵌入的凭证"""
    return re.sub(r"(https?://)[^/@]*@", r"\1***@", url)


def _dashboard_git_args():
    """
    通过临时 credential helper 提供 token：token 只存在于环境变量中，
    不会出现在 URL、命令行参数或 .git/config 里。用户名固定为
    x-access-token，对 GitHub PAT（classic/fine-grained）及 GitCode 均有效。
    """
    token = os.environ.get("DASHBOARD_GIT_TOKEN", "").strip()
    if not token:
        return []
    helper = (
        '!f() { '
        'echo "username=x-access-token"; '
        'echo "password=${DASHBOARD_GIT_TOKEN}"; '
        '}; f'
    )
    return ["-c", "credential.helper=", "-c", f"credential.helper={helper}"]


def _dashboard_git(args, cwd=None):
    env = {**os.environ, "GIT_TERMINAL_PROMPT": "0"}
    return subprocess.call(["git", *_dashboard_git_args(), *args], cwd=cwd, env=env)


def _commit_and_push_dashboard(clone_dir: str, branch: str,
                               message: str, attempts: int = 3) -> bool:
    """
    提交并推送报告。并发构建写同一报告仓库时 push 会被拒（non-fast-forward），
    rebase 到远端分支后重试；报告路径互不重叠，rebase 极少冲突。
    """
    if subprocess.call(["git", "add", "-A"], cwd=clone_dir) != 0:
        click.secho("Failed to stage dashboard report changes", fg="red")
        return False
    commit = [
        "git",
        "-c", f"user.name={DASHBOARD_COMMIT_NAME}",
        "-c", f"user.email={DASHBOARD_COMMIT_EMAIL}",
        "commit", "-m", message,
    ]
    if subprocess.call(commit, cwd=clone_dir) != 0:
        click.secho("Failed to commit dashboard report changes", fg="red")
        return False
    for attempt in range(1, attempts + 1):
        if _dashboard_git(["push", "origin", branch], cwd=clone_dir) == 0:
            return True
        click.secho(
            f"Dashboard push attempt {attempt}/{attempts} rejected, rebase and retry.",
            fg="yellow"
        )
        if _dashboard_git(["pull", "--rebase", "origin", branch], cwd=clone_dir) != 0:
            click.secho("Failed to rebase onto remote branch, abort.", fg="red")
            subprocess.call(["git", "rebase", "--abort"], cwd=clone_dir)
            return False
    return False


def _prune_version_dir(version_dir: str, keep: int = 3) -> List[str]:
    """
    每个版本目录最多保留 keep 份最新的未合入报告（按文件名时间戳排序）；
    PR_merged=true 的报告永不删除（保护 docker-images 等其他生产方的合入数据）。
    """
    files = sorted(
        (f for f in os.listdir(version_dir) if f.endswith(".json")),
        reverse=True,
    )
    removed = []
    non_merged = []
    for name in files:
        merged = False
        try:
            with open(os.path.join(version_dir, name), "r", encoding="utf-8") as f:
                merged = bool(json.load(f).get("PR_merged"))
        except (OSError, ValueError):
            pass
        if not merged:
            non_merged.append(name)
    while len(non_merged) > keep:
        old = non_merged.pop()
        os.remove(os.path.join(version_dir, old))
        removed.append(old)
    return removed


def push_dashboard(reports: List[Tuple[dict, str, str]]) -> bool:
    """
    推送报告到 upstream-dashboard 仓库的 KunpengTest 分支（默认开启）。
    DASHBOARD_REPO_URL / DASHBOARD_GIT_TOKEN 任一缺失时告警跳过，
    不影响 CI 主流程。
    """
    if not reports:
        return True
    if DRY_RUN:
        click.secho("DRY_RUN: skip dashboard push", fg="yellow")
        return True
    repo = DASHBOARD_REPO_URL.strip()
    if not repo:
        click.secho("DASHBOARD_REPO_URL not set, skip dashboard push", fg="yellow")
        return True
    if not os.environ.get("DASHBOARD_GIT_TOKEN", "").strip():
        click.secho("DASHBOARD_GIT_TOKEN not set, skip dashboard push", fg="yellow")
        return True

    clone_dir = tempfile.mkdtemp(prefix="dashboard-push-")
    ok = False
    try:
        click.secho(
            f"Cloning dashboard repo: {_sanitize_repo(repo)} (branch={DASHBOARD_BRANCH})",
            fg="blue"
        )
        if _dashboard_git(
            ["clone", "--depth", "1", "-b", DASHBOARD_BRANCH, repo, clone_dir]
        ) != 0:
            click.secho("Failed to clone dashboard repo", fg="red")
            return False

        version_dirs = set()
        for report, filename, relpath in reports:
            dest = os.path.join(clone_dir, relpath)
            os.makedirs(os.path.dirname(dest), exist_ok=True)
            with open(dest, "w", encoding="utf-8") as f:
                json.dump(report, f, ensure_ascii=False, indent=2)
            version_dirs.add(os.path.dirname(dest))

        removed = []
        for vdir in version_dirs:
            removed.extend(_prune_version_dir(vdir))

        message = f"conda-ecopkgs: add {len(reports)} test report(s)"
        ok = _commit_and_push_dashboard(clone_dir, DASHBOARD_BRANCH, message)
    finally:
        shutil.rmtree(clone_dir, ignore_errors=True)
    if ok:
        extra = f", pruned {len(removed)} old report(s)" if removed else ""
        click.secho(
            f"Pushed {len(reports)} report(s) to dashboard{extra}", fg="green"
        )
    else:
        click.secho("Failed to push dashboard reports", fg="red")
    return ok


# ---------------------------------------------------------------------------
# PR 评论（参考 eulerpublisher 的 comment_publish_result）
# ---------------------------------------------------------------------------

def comment_results(pr_id, results: List[dict]) -> bool:
    if not pr_id:
        return True
    token = os.environ.get("GITCODE_API_TOKEN")
    if not results:
        return True
    if DRY_RUN or not token:
        click.secho("DRY_RUN or no GITCODE_API_TOKEN: skip PR comment", fg="yellow")
        click.echo(_comment_html(results))
        return True

    url = f"{REPOSITORY_REQUEST_URL}/{pr_id}/comments"
    body = {"body": _comment_html(results)}
    try:
        response = requests.post(
            url=url,
            headers={"private-token": token},
            json=body,
            timeout=30,
        )
        if response.status_code not in (200, 201):
            click.secho(
                f"Comment pull request failed: {response.status_code}",
                fg="red"
            )
            return False
        click.secho("Comment posted to PR", fg="green")
        return True
    except requests.RequestException as e:
        click.secho(f"Comment pull request failed: {e}", fg="red")
        return False


def _comment_html(results: List[dict]) -> str:
    head = (
        "<tr><th>Package</th><th>OS Version</th><th>Pkg Version</th>"
        "<th>Arch</th><th>Install</th><th>Tests</th><th>Result</th></tr>"
    )
    body = ""
    for r in results:
        passed = r["status"] == "passed"
        install_cell = ":white_check_mark:SUCCESS" if r["install"] == "success" else ":x:FAILED"
        total = len(r["tests"])
        passed_n = sum(1 for t in r["tests"] if t.get("result") == "pass")
        tests_cell = f"{passed_n}/{total}" if total else "-"
        result_cell = ":white_check_mark:SUCCESS" if passed else ":x:FAILED"
        row = (
            f"<tr><td>{r['package']}</td><td>{r['os_version']}</td>"
            f"<td>{r['package_version']}</td><td>{r['arch']}</td>"
            f"<td>{install_cell}</td><td>{tests_cell}</td><td>{result_cell}</td></tr>"
        )
        body += row
        failed = [t for t in r["tests"] if t.get("result") in ("fail", "error")]
        for t in failed:
            msg = (t.get("message") or "").replace("\n", "<br>")[:1000]
            body += (
                f"<tr><td colspan=7><details><summary>"
                f":x: {r['package']} - {t['name']} ({t.get('result')})</summary>"
                f"<pre>{msg}</pre></details></td></tr>"
            )
    return f"<p>conda-ecopkgs CI verification results (aarch64):</p><table>{head}{body}</table>"


# ---------------------------------------------------------------------------
# 本地模式（无 PR / 无 token，直接在仓库目录验证指定包）
# ---------------------------------------------------------------------------

def run_local(args) -> int:
    base = os.path.abspath(".")
    package = args.local_pkg
    pkg_dir = os.path.join(base, "packages", package)
    if not os.path.isdir(pkg_dir):
        click.secho(f"Package dir not found: {pkg_dir}", fg="red")
        return 1

    target_oses = read_target_oses(base)
    os_version = args.local_os or (target_oses[0] if target_oses else None)
    if not os_version:
        click.secho("No OS version given and config/os-versions.txt is empty", fg="red")
        return 1

    supported = parse_yaml_data(os.path.join(pkg_dir, SUPPORTED_VERSIONS_FILE))
    versions = supported.get(os_version, {})
    if not versions:
        click.secho(f"{os_version} not found in {package}/{SUPPORTED_VERSIONS_FILE}", fg="red")
        return 1

    machine_arch = args.arch or platform.machine()
    package_version = args.local_pkg_version
    if not package_version:
        for ver, arches in versions.items():
            if machine_arch in arches or "noarch" in arches:
                package_version = ver
                break
        if not package_version:
            click.secho(
                f"No version for arch {machine_arch} under {os_version}", fg="red"
            )
            return 1

    arches = versions.get(package_version, [])
    if machine_arch not in arches and "noarch" not in arches:
        click.secho(
            f"{package_version} under {os_version} does not list {machine_arch}",
            fg="red"
        )
        return 1

    click.secho(
        f"Local verify: {package} {os_version} {package_version} "
        f"[{machine_arch}] (tests: on)", fg="blue"
    )

    entry = verify_package(
        base, package, os_version, package_version,
        with_tests=True, update_dir=base, origin_dir=base
    )
    entry["tested"] = True

    hardware = args.hardware or os.environ.get("HARDWARE", "kunpeng920")
    report, filename, relpath = make_report_file(entry, hardware)
    reports_root = os.path.join(base, "reports")
    write_reports([(report, filename, relpath)], reports_root)

    status = click.style("PASSED", fg="green") if entry["status"] == "passed" \
        else click.style("FAILED", fg="red")
    click.echo(f"Result: {status}")
    click.echo(f"Report: {os.path.join(reports_root, relpath)}")

    return 0 if entry["status"] == "passed" else 1


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


def _request(url: str, headers: dict = None):
    cnt = 0
    response = None
    while (not response) and (cnt < 20):
        response = requests.get(url=url, headers=headers)
        cnt += 1
    return response


def get_change_files(pr_id) -> List[str]:
    change_files = []
    url = f"{REPOSITORY_REQUEST_URL}/{pr_id}/files"
    headers = {"private-token": os.environ["GITCODE_API_TOKEN"]}
    response = _request(url=url, headers=headers)
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
    headers = {"private-token": os.environ["GITCODE_API_TOKEN"]}
    response = _request(url=url, headers=headers)
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

    # 本地模式参数（无 PR / 无 token，直接验证指定包）
    new_parser.add_argument(
        "-lp", "--local-pkg", help="local mode: package name to verify"
    )
    new_parser.add_argument(
        "-lo", "--local-os", help="local mode: OS version (default: first in config/os-versions.txt)"
    )
    new_parser.add_argument(
        "-lpv", "--local-pkg-version",
        help="local mode: package version (default: first version for arch)"
    )
    new_parser.add_argument(
        "-ar", "--arch", help="local mode: override machine arch"
    )
    new_parser.add_argument(
        "-hw", "--hardware", help="local mode: hardware for report (kunpeng920/kunpeng950)"
    )
    return new_parser


def clear_all(work_dir: str):
    if not os.path.exists(work_dir):
        return
    shutil.rmtree(work_dir)


def run_ci(args) -> int:
    # create workdir
    work_dir = os.path.join(DEFAULT_WORKDIR, args.source_repo)
    if os.path.exists(work_dir):
        shutil.rmtree(work_dir)

    if pull_source_code(
            args.prid,
            args.source_branch,
            work_dir
    ):
        return 1

    if pull_origin_code(work_dir):
        return 1

    success, results = verify_updates(args.prid, work_dir)

    # PR 评论报告结果（无 token / DRY_RUN 时仅打印）
    comment_results(args.prid, results)

    # 生成 KunpengTest 报告并推送 dashboard（按环境变量开关）
    hardware = os.environ.get("HARDWARE", "kunpeng920")
    job_url = os.environ.get("JOB_URL")
    tested = [r for r in results if r.get("tested")]
    reports = [make_report_file(r, hardware, args.prid, job_url) for r in tested]
    write_reports(reports, work_dir)
    push_dashboard(reports)

    clear_all(work_dir)
    return 0 if success else 1


if __name__ == "__main__":
    parser = init_parser()
    args = parser.parse_args()

    if args.local_pkg:
        sys.exit(run_local(args))

    if (
            not args.prid
            or not args.source_repo
            or not args.source_branch
    ):
        parser.print_help()
        sys.exit(1)

    sys.exit(run_ci(args))
