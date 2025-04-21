#!/bin/bash
set -ex

PACKAGE=""
CHANNEL=""
VERSION=""
DEPENDENCY_CHANNELS=()

show_help() {
  echo "Usage: $0 -p <package> -c <primary_channel> [-d <dependency_channel>...] [-v <version>]"
  echo "Example:"
  echo "  $0 -p numpy -c conda-forge -d defaults -v 1.21.0"
  echo "  $0 -p pandas -c my-channel -d conda-forge -d defaults"
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

for channel in "${DEPENDENCY_CHANNELS[@]}"; do
  echo "Adding dependency channel: $channel"
  conda config --add channels "$channel" || {
    echo "Warning: Failed to add channel $channel" >&2
  }
done

conda update -y -n base conda

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
