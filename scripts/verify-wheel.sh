#!/usr/bin/env bash
set -eu
core_root=$(cd -- "$(dirname -- "$0")/.." && pwd)
core_check=$(mktemp -d)
trap 'rm -rf -- "$core_check"' EXIT
uv build --project "$core_root" --out-dir "$core_check/dist"
uv venv --python 3.13 "$core_check/venv"
uv pip install --python "$core_check/venv/bin/python" "$core_check"/dist/*.whl pytest
cp -R "$core_root/tests" "$core_root/examples" "$core_check/"
cd -- "$core_check"
"$core_check/venv/bin/python" -I -c 'import corpora_linking; print(corpora_linking.__file__)'
"$core_check/venv/bin/python" -m pytest tests
for core_example in manual_link resolve_link scholarly_link; do
  "$core_check/venv/bin/python" "examples/$core_example.py"
done
