#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'EOF'
Usage: New-AnsysAgentLinuxBundle.sh --output-directory DIR --codebase-memory-binary PATH [--repository-root DIR] [--python PATH]

Run this on a connected Linux x86_64 CPython 3.12 build host. The supplied
codebase-memory-mcp binary is copied into the bundle so the target never needs
GitHub access.
EOF
}

repository_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
output_directory=""
python_exe="python3.12"
native_binary=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    --output-directory) output_directory=$2; shift 2 ;;
    --repository-root) repository_root=$(realpath "$2"); shift 2 ;;
    --python) python_exe=$2; shift 2 ;;
    --codebase-memory-binary) native_binary=$(realpath "$2"); shift 2 ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Unknown option: $1" >&2; usage >&2; exit 2 ;;
  esac
done
[[ -n "$output_directory" && -n "$native_binary" && -x "$native_binary" ]] || { usage >&2; exit 2; }
[[ "$(uname -s)" == "Linux" && "$(uname -m)" == "x86_64" ]] || { echo "Build host must be Linux x86_64" >&2; exit 2; }
"$python_exe" -c 'import sys; assert sys.version_info[:2] == (3, 12), sys.version'
cd "$repository_root"
uv lock --check
version=$("$python_exe" -c 'import tomllib; print(tomllib.load(open("pyproject.toml", "rb"))["project"]["version"])')
name="ansys-agent-${version}-linux-x86_64-py312"
mkdir -p "$output_directory"
[[ ! -e "$output_directory/$name.tar.gz" ]] || { echo "Output already exists" >&2; exit 2; }
staging=$(mktemp -d)
trap 'rm -rf "$staging"' EXIT
root="$staging/$name"
mkdir -p "$root/runtime" "$root/wheelhouse" "$root/tools/codebase-memory-mcp/0.9.0"
tar --exclude=.git --exclude=.venv --exclude=__pycache__ --exclude=.pytest_cache --exclude=.aedt-agent --exclude='*.pyc' -cf - . | tar -C "$root/runtime" -xf -
rm -rf "$root/runtime/.git" "$root/runtime/.venv" "$root/runtime/.aedt-agent"
"$python_exe" -m pip download --dest "$root/wheelhouse" 'setuptools>=69' wheel
"$python_exe" -m pip wheel --wheel-dir "$root/wheelhouse" "$repository_root[linux]"
install -m 0755 "$native_binary" "$root/tools/codebase-memory-mcp/0.9.0/codebase-memory-mcp"
native_sha=$(sha256sum "$root/tools/codebase-memory-mcp/0.9.0/codebase-memory-mcp" | awk '{print $1}')
git_revision=$(git rev-parse HEAD)
"$python_exe" - "$root/bundle.json" "$version" "$git_revision" "$native_sha" <<'PY'
import json, sys
path, version, revision, native_sha = sys.argv[1:]
json.dump({"schema_version": 1, "project": {"name": "aedt-agent", "version": version, "git_revision": revision}, "target": {"os": "linux", "architecture": "x86_64", "python": "3.12"}, "native_tools": {"codebase_memory_mcp": {"version": "0.9.0", "platform": "linux-x86_64", "path": "tools/codebase-memory-mcp/0.9.0/codebase-memory-mcp", "sha256": native_sha}}}, open(path, "w", encoding="utf-8"), ensure_ascii=True, indent=2)
PY
(cd "$root" && find . -type f ! -name SHA256SUMS -print0 | sort -z | xargs -0 sha256sum > SHA256SUMS)
tar -C "$staging" -czf "$output_directory/$name.tar.gz" "$name"
sha256sum "$output_directory/$name.tar.gz" > "$output_directory/$name.tar.gz.sha256"
printf '%s\n' "$output_directory/$name.tar.gz"
