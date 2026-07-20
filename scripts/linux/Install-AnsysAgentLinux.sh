#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'EOF'
Usage: Install-AnsysAgentLinux.sh --bundle-root DIR [--install-root DIR] [--python PATH] [--verify-only] [--skip-knowledge-prepare]
EOF
}

bundle_root=""
install_root="$HOME/ansys-agent"
python_exe="python3.12"
verify_only=0
skip_knowledge_prepare=0
while [[ $# -gt 0 ]]; do
  case "$1" in
    --bundle-root) bundle_root=$2; shift 2 ;;
    --install-root) install_root=$2; shift 2 ;;
    --python) python_exe=$2; shift 2 ;;
    --verify-only) verify_only=1; shift ;;
    --skip-knowledge-prepare) skip_knowledge_prepare=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Unknown option: $1" >&2; usage >&2; exit 2 ;;
  esac
done
[[ -n "$bundle_root" ]] || { usage >&2; exit 2; }
[[ "$(uname -s)" == "Linux" && "$(uname -m)" == "x86_64" ]] || { echo "This installer requires Linux x86_64" >&2; exit 2; }
bundle_root=$(realpath "$bundle_root")
manifest="$bundle_root/bundle.json"
checksums="$bundle_root/SHA256SUMS"
[[ -f "$manifest" && -f "$checksums" && -d "$bundle_root/runtime/src/aedt_agent" && -d "$bundle_root/wheelhouse" ]] || { echo "Invalid Linux bundle" >&2; exit 2; }
python3 - "$manifest" <<'PY'
import json, sys
data = json.load(open(sys.argv[1], encoding="utf-8"))
target = data.get("target", {})
native = data.get("native_tools", {}).get("codebase_memory_mcp", {})
if data.get("schema_version") != 1 or target.get("os") != "linux" or target.get("architecture") != "x86_64":
    raise SystemExit("This installer accepts only Linux x86_64 bundles")
if native.get("platform") != "linux-x86_64" or not isinstance(native.get("path"), str):
    raise SystemExit("Bundle is missing the Linux codebase-memory-mcp executable")
PY
(cd "$bundle_root" && sha256sum --strict --check SHA256SUMS)
if [[ $verify_only -eq 1 ]]; then
  printf '{"status":"verified","bundle_root":"%s"}\n' "$bundle_root"
  exit 0
fi
"$python_exe" -c 'import sys; assert sys.version_info[:2] == (3, 12), sys.version'
if [[ -e "$install_root" ]]; then
  echo "Install root already exists: $install_root" >&2
  exit 2
fi
mkdir -p "$(dirname "$install_root")"
cp -a "$bundle_root/runtime" "$install_root"
"$python_exe" -m venv "$install_root/.venv"
"$install_root/.venv/bin/python" -m pip install --no-index --find-links "$bundle_root/wheelhouse" 'setuptools>=69' wheel
"$install_root/.venv/bin/python" -m pip install --no-index --find-links "$bundle_root/wheelhouse" --editable "$install_root[linux]"
native_path=$(python3 - "$manifest" <<'PY'
import json, sys
print(json.load(open(sys.argv[1], encoding="utf-8"))["native_tools"]["codebase_memory_mcp"]["path"])
PY
)
install -m 0755 "$bundle_root/$native_path" "$install_root/.venv/bin/codebase-memory-mcp"
if [[ $skip_knowledge_prepare -eq 0 ]]; then
  "$install_root/.venv/bin/ansys-api-memory" prepare
fi
"$install_root/.venv/bin/python" -m pip check
printf '{"status":"installed","install_root":"%s"}\n' "$install_root"
