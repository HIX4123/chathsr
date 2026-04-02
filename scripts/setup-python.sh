#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'EOF'
Usage: ./scripts/setup-python.sh [3.12.12|3.12.10]

Downloads a project-local standalone Python build into ./.python/,
recreates ./.venv with the selected interpreter, and installs the
project in editable dev mode.

Default version: 3.12.12
EOF
}

require_cmd() {
  if ! command -v "$1" >/dev/null 2>&1; then
    printf 'Missing required command: %s\n' "$1" >&2
    exit 1
  fi
}

if [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
  usage
  exit 0
fi

VERSION="${1:-3.12.12}"
case "$VERSION" in
  3.12.12|3.12.10)
    ;;
  *)
    printf 'Unsupported Python version: %s\n' "$VERSION" >&2
    usage >&2
    exit 2
    ;;
esac

if [[ "$(uname -s)" != "Linux" ]]; then
  printf 'This setup script currently supports Linux hosts only.\n' >&2
  exit 1
fi

if [[ "$(uname -m)" != "x86_64" ]]; then
  printf 'This setup script currently supports x86_64 hosts only.\n' >&2
  exit 1
fi

require_cmd curl
require_cmd tar
require_cmd python3

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON_DIR="$ROOT_DIR/.python"
DISTFILES_DIR="$PYTHON_DIR/distfiles"
INSTALL_DIR="$PYTHON_DIR/$VERSION"
VENV_DIR="$ROOT_DIR/.venv"
RELEASES_API="https://api.github.com/repos/astral-sh/python-build-standalone/releases?per_page=5"
tmp_dir=""

mkdir -p "$DISTFILES_DIR"
metadata_file="$(mktemp "$PYTHON_DIR/.releases.XXXXXX.json")"
trap 'rm -rf "${tmp_dir:-}" "${metadata_file:-}"' EXIT

printf 'Resolving standalone Python %s asset...\n' "$VERSION"
asset_info=""
for page in $(seq 1 25); do
  curl -fsSL \
    --retry 3 \
    --retry-all-errors \
    -H 'Accept: application/vnd.github+json' \
    "${RELEASES_API}&page=${page}" \
    > "$metadata_file"

  page_asset_info="$(
    python3 - "$VERSION" "$metadata_file" <<'PY'
import json
import sys

version = sys.argv[1]
metadata_path = sys.argv[2]
prefix = f"cpython-{version}+"
suffix = "-x86_64-unknown-linux-gnu-install_only_stripped.tar.gz"

try:
    with open(metadata_path, "r", encoding="utf-8") as fh:
        releases = json.load(fh)
except json.JSONDecodeError as exc:
    raise SystemExit(f"Failed to parse GitHub release metadata: {exc}") from exc

if not releases:
    print("__END__")
    raise SystemExit(0)

for release in releases:
    for asset in release.get("assets", []):
        name = asset.get("name", "")
        if name.startswith(prefix) and name.endswith(suffix):
            print(name)
            print(asset["browser_download_url"])
            raise SystemExit(0)
PY
  )"

  if [[ "$page_asset_info" == "__END__" ]]; then
    break
  fi

  if [[ -n "$page_asset_info" ]]; then
    asset_info="$page_asset_info"
    break
  fi
done

if [[ -z "$asset_info" ]]; then
  printf 'Could not find a Linux x86_64 install_only_stripped asset for Python %s.\n' "$VERSION" >&2
  exit 1
fi

asset_name="$(printf '%s\n' "$asset_info" | sed -n '1p')"
asset_url="$(printf '%s\n' "$asset_info" | sed -n '2p')"
archive_path="$DISTFILES_DIR/$asset_name"

if [[ ! -f "$archive_path" ]]; then
  printf 'Downloading %s...\n' "$asset_name"
  curl -fL --retry 3 --output "$archive_path" "$asset_url"
else
  printf 'Using cached archive %s\n' "$archive_path"
fi

tmp_dir="$(mktemp -d "$PYTHON_DIR/.tmp.XXXXXX")"

printf 'Extracting Python %s...\n' "$VERSION"
tar -xzf "$archive_path" -C "$tmp_dir"

if [[ -d "$tmp_dir/python" ]]; then
  extracted_dir="$tmp_dir/python"
else
  first_dir="$(find "$tmp_dir" -mindepth 1 -maxdepth 1 -type d | head -n 1)"
  if [[ -z "$first_dir" ]]; then
    printf 'Could not locate the extracted Python directory.\n' >&2
    exit 1
  fi
  extracted_dir="$first_dir"
fi

rm -rf "$INSTALL_DIR.tmp"
mv "$extracted_dir" "$INSTALL_DIR.tmp"
rm -rf "$INSTALL_DIR"
mv "$INSTALL_DIR.tmp" "$INSTALL_DIR"

interpreter_path="$INSTALL_DIR/bin/python3.12"
if [[ ! -x "$interpreter_path" ]]; then
  interpreter_path="$INSTALL_DIR/bin/python3"
fi
if [[ ! -x "$interpreter_path" ]]; then
  printf 'Standalone Python interpreter was not found under %s/bin.\n' "$INSTALL_DIR" >&2
  exit 1
fi

if [[ -d "$VENV_DIR" ]]; then
  printf 'Removing existing virtualenv %s...\n' "$VENV_DIR"
  rm -rf "$VENV_DIR"
fi

printf 'Creating virtualenv with %s...\n' "$interpreter_path"
"$interpreter_path" -m venv "$VENV_DIR"
"$VENV_DIR/bin/python" -m ensurepip --upgrade
"$VENV_DIR/bin/python" -m pip install --upgrade pip
"$VENV_DIR/bin/python" -m pip install -e "$ROOT_DIR[dev]"

printf '\nPython %s is ready.\n' "$VERSION"
printf 'Activate the environment with: source %s/bin/activate\n' "$VENV_DIR"
printf 'Interpreter: %s\n' "$VENV_DIR/bin/python"
