#!/bin/bash
set -ex

PACKAGE=""
CHANNEL=""
VERSION=""
DEPENDENCY_CHANNELS=()
TEST_SPEC=""
RESULTS_DIR=""

show_help() {
  echo "Usage: $0 -p <package> -c <primary_channel> [-d <dependency_channel>...] [-v <version>] [-t <test-spec.json>] [-r <results_dir>]"
  echo "Example:"
  echo "  $0 -p numpy -c conda-forge -d defaults -v 1.21.0"
  echo "  $0 -p pandas -c my-channel -d conda-forge -d defaults"
  echo "  $0 -p bcrypt -c conda-forge -v 4.3.0 -t /tmp/test-spec.json -r /results"
  exit 1
}

while [[ $# -gt 0 ]]; do
  case $1 in
    -p)
      [[ -z "$2" ]] && {
        echo "Error: -p requires a package name" >&2
        show_help
      }
      PACKAGE="$2"
      shift 2
      ;;

    -c)
      [[ -z "$2" || $2 =~ ^- ]] && {
        echo "Error: -c requires a single channel" >&2
        show_help
      }
      CHANNEL="$2"
      shift 2
      ;;

    -v)
      [[ -z "$2" ]] && {
        echo "Error: -v requires a version number" >&2
        show_help
      }
      VERSION="$2"
      shift 2
      ;;

    -d)
      shift
      while [[ $# -gt 0 && ! $1 =~ ^- ]]; do
        DEPENDENCY_CHANNELS+=("$1")
        shift
      done
      ;;

-t)
      [[ -z "$2" ]] && {
        echo "Error: -t requires a test spec json path" >&2
        show_help
      }
      TEST_SPEC="$2"
      shift 2
      ;;

-r)
      [[ -z "$2" ]] && {
        echo "Error: -r requires a results directory path" >&2
        show_help
      }
      RESULTS_DIR="$2"
      shift 2
      ;;

    -h|--help)
      show_help
      ;;

    *)
      echo "Error: Unknown option or missing argument: $1" >&2
      show_help
      ;;
  esac
done

[[ -z "$PACKAGE" || -z "$CHANNEL" ]] && { echo "Error: -p and -c are required" >&2; show_help; }

source /usr/local/miniconda/etc/profile.d/conda.sh
conda create -y -n $PACKAGE
conda activate $PACKAGE

for channel in "${DEPENDENCY_CHANNELS[@]}"; do
  echo "Adding dependency channel: $channel"
  conda config --add channels "$channel" || {
    echo "Warning: Failed to add channel $channel" >&2
  }
done

INSTALL_SPEC="${PACKAGE}"
[[ -n "$VERSION" ]] && INSTALL_SPEC="${PACKAGE}=${VERSION}"

echo "Installing ${INSTALL_SPEC} from channel ${CHANNEL}"

MAX_RETRIES=3
RETRY_DELAY=5
count=0

while (( count < MAX_RETRIES )); do
  echo "Attempt $((count+1)) to install package..."
  if conda install -y --quiet -c "$CHANNEL" "$INSTALL_SPEC" > /dev/null; then
    break
  else
    echo "Install failed, retrying in $RETRY_DELAY seconds..."
    sleep "$RETRY_DELAY"
    ((count++))
  fi
done

if (( count == MAX_RETRIES )); then
  echo "Error: Installation failed after $MAX_RETRIES attempts"
  exit 1
fi

INSTALLED_VERSION=$(conda list "$PACKAGE" 2>/dev/null | awk -v pkg="$PACKAGE" '$1 == pkg {print $2}')
if [[ -z "$INSTALLED_VERSION" ]]; then
  echo "ERROR: ${PACKAGE} installation failed!" >&2
  exit 1
else
  echo "Successfully installed ${PACKAGE} version: ${INSTALLED_VERSION}"
  echo "Primary channel: $CHANNEL"
fi

# 安装成功后执行测试用例（由 update.py 生成 test spec 并挂载）
TEST_RUNNER="${TEST_RUNNER:-/tmp/test-runner.py}"
if [[ -n "$TEST_SPEC" ]]; then
  if [[ ! -f "$TEST_SPEC" ]]; then
    echo "WARN: test spec not found: ${TEST_SPEC}, skipping tests"
  elif [[ ! -f "$TEST_RUNNER" ]]; then
    echo "WARN: test runner not found: ${TEST_RUNNER}, skipping tests"
  else
    echo "Running test cases from ${TEST_SPEC}"
    mkdir -p "${RESULTS_DIR:-/results}"
    # 新建的 conda 环境不一定带 python（例如 wannier90 依赖闭包里没有它），
    # 这里优先用 conda 基础解释器，再回退到系统 python3/python。
    PYTHON_BIN="${CONDA_PYTHON_EXE:-$(command -v python3 || command -v python)}"
    "$PYTHON_BIN" "$TEST_RUNNER" "$TEST_SPEC" "${RESULTS_DIR:-/results}" || {
      echo "ERROR: test cases failed for ${PACKAGE}!" >&2
      exit 1
    }
  fi
fi
