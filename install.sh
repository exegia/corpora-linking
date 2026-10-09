#!/bin/sh
# shellcheck disable=SC2249,SC2250,SC2292,SC2310,SC2311,SC2312
# Homebrew's `brew style` runs shellcheck --enable=all over the tap's *.sh
# files (effectively in bash dialect). This is a POSIX sh installer that
# intentionally uses unbraced `$var`, `[ ]` tests, and `cmd || die`; the
# disabled checks are the --enable=all style/info nits those choices trip, not
# correctness issues (errors are handled explicitly via || die / || true /
# || continue; set -e is only a backstop).
# corpora — portable installer (curl | bash).
#
# Mirrors the Homebrew formula (Formula/cli.rb) without Homebrew: it builds
# the corpora-cli package from a release-tag tarball into its own virtualenv,
# lets pip/uv resolve the corpora-py dependency closure from PyPI, then
# replays the three fix-ups the formula does that a naive `pip install` would
# miss — and one of them breaks on macOS without it:
#
#   1. Reinstall corpora-cli --force-reinstall --no-deps so its `corpora`
#      script wins the entry-point clash with corpora-py < 2.3.0
#      (Formula/cli.rb:33-37).
#   2. Strip the REPL/serving extras the CLI never reaches
#      (uvloop watchfiles jedi parso) (Formula/cli.rb:44-45).
#   3. On macOS, re-sign every native extension (.so/.dylib) — pip's ad-hoc
#      signatures are invalid and the kernel SIGKILLs them on Apple Silicon
#      the moment such a page is imported (Formula/cli.rb:60-66).
#
# uv is used if present (faster resolver, the same one the Makefile uses);
# falls back to the venv module + pip. POSIX sh, shellcheck-clean.
set -eu

REPO="exegia/homebrew-corpora"
PREFIX="${CORPORA_HOME:-$HOME/.corpora}"
VENV="$PREFIX/venv"
LOCAL_BIN="${CORPORA_BIN:-$HOME/.local/bin}"

# --- color (NO_COLOR honoured, drops on non-tty) ---------------------------
if [ -n "${NO_COLOR:-}" ] || [ ! -t 1 ]
then
  C_RESET=""
  C_DIM=""
  C_GREEN=""
  C_YELLOW=""
  C_RED=""
else
  C_RESET=$(printf '\033[0m')
  C_DIM=$(printf '\033[2m')
  C_GREEN=$(printf '\033[32m')
  C_YELLOW=$(printf '\033[33m')
  C_RED=$(printf '\033[31m')
fi

log() { printf '%s%s%s\n' "$C_GREEN" "$*" "$C_RESET" >&2; }
dim() { printf '%s%s%s\n' "$C_DIM" "$*" "$C_RESET" >&2; }
warn() { printf '%s%s%s\n' "$C_YELLOW" "warn: $*" "$C_RESET" >&2; }
die() {
  printf '%s%s%s\n' "$C_RED" "error: $*" "$C_RESET" >&2
  exit 1
}

usage() {
  cat <<EOF
Usage: curl -fsSL https://raw.githubusercontent.com/exegia/homebrew-corpora/main/install.sh | bash

Installs corpora, corpora-api and cf-mcp into $HOME/.local/bin from a
release-tag tarball (latest by default), into its own virtualenv.
Re-running it over an existing install recreates the virtualenv, so the
same command also upgrades (or downgrades, with --version) in place.

To pass options through curl | bash, use 'bash -s --', e.g.
  curl -fsSL <url> | bash -s -- --version v0.1.1

Options:
  --version vX.Y.Z   Install a specific release tag (X.Y.Z or vX.Y.Z)
  --uninstall        Remove corpora and its virtualenv
  -h, --help         Show this help

Environment:
  CORPORA_VERSION   Same as --version
  CORPORA_HOME      Install root (default: ~/.corpora)
  CORPORA_BIN       Bin symlink dir (default: ~/.local/bin)
EOF
}

# --- arg parsing -----------------------------------------------------------
VERSION=""
UNINSTALL=0
while [ $# -gt 0 ]
do
  case "$1" in
    --version)
      shift
      [ $# -gt 0 ] || die "--version needs a value"
      VERSION="$1"
      ;;
    --version=*) VERSION="${1#*=}" ;;
    --uninstall) UNINSTALL=1 ;;
    -h | --help)
      usage
      exit 0
      ;;
    *) die "unknown option: $1 (try --help)" ;;
  esac
  shift
done

# CORPORA_VERSION env is the fallback pin (flag wins).
: "${CORPORA_VERSION:=}"
[ -n "$VERSION" ] || VERSION="$CORPORA_VERSION"

# --- helpers ---------------------------------------------------------------
# corpora-cli needs Python >= 3.13. Print the first suitable interpreter, or
# return non-zero (the caller prints the install hint).
find_python() {
  for cand in python3.13 python3.14 python3
  do
    command -v "$cand" >/dev/null 2>&1 || continue
    ver=$("$cand" -c 'import sys;print("%d.%d"%sys.version_info[0:2])' 2>/dev/null) || continue
    case "$ver" in
      3.1[3-9] | 3.[2-9][0-9] | [4-9].*)
        printf '%s' "$cand"
        return 0
        ;;
    esac
  done
  return 1
}

# Resolve the release tag: an explicit pin wins, else follow GitHub's
# releases/latest redirect — no jq/gh dependency, just the Location header.
resolve_tag() {
  if [ -n "$VERSION" ]
  then
    v=$(printf '%s' "$VERSION" | sed 's/^v//')
    case "$v" in
      [0-9]*.[0-9]*.[0-9]*)
        printf 'v%s' "$v"
        return 0
        ;;
      *)
        printf "error: '%s' is not a valid X.Y.Z version\n" "$VERSION" >&2
        return 1
        ;;
    esac
  fi
  loc=$(curl -fsSI "https://github.com/$REPO/releases/latest" 2>/dev/null |
    tr -d '\r' |
    awk 'tolower($1)=="location:"{print $2}')
  if [ -z "$loc" ]
  then
    printf 'error: could not resolve the latest release of %s\n' "$REPO" >&2
    return 1
  fi
  printf '%s' "${loc##*/}"
}

# Link one command into ~/.local/bin without clobbering a foreign file.
link_bin() {
  name=$1
  target="$VENV/bin/$name"
  link="$LOCAL_BIN/$name"
  mkdir -p "$LOCAL_BIN"
  if [ -e "$link" ] && [ ! -L "$link" ]
  then
    warn "$link exists and is not a symlink — leaving it in place"
    return 0
  fi
  ln -sfn "$target" "$link"
}

# Remove only a corpora-owned symlink.
unlink_bin() {
  name=$1
  link="$LOCAL_BIN/$name"
  [ -L "$link" ] || return 0
  case "$(readlink "$link")" in
    "$VENV/bin/"*) rm -f "$link" ;;
    *) warn "$link is not a corpora symlink — leaving it in place" ;;
  esac
}

# --- uninstall --------------------------------------------------------------
if [ "$UNINSTALL" = 1 ]
then
  log "Removing corpora"
  for b in corpora corpora-api cf-mcp; do unlink_bin "$b"; done
  if [ -d "$VENV" ]
  then
    rm -rf "$VENV"
    dim "removed $VENV"
  fi
  # rmdir only succeeds when PREFIX is now empty — leaves user data alone.
  rmdir "$PREFIX" 2>/dev/null && dim "removed $PREFIX" || true
  log "Done. corpora uninstalled."
  exit 0
fi

# --- install ---------------------------------------------------------------
command -v curl >/dev/null 2>&1 || die "curl is required (https://curl.se)"
[ -d "$HOME" ] || die "HOME is not set"

py=$(find_python) || die "Python >= 3.13 not found. Install python@3.13 first:
  macOS:  brew install python@3.13
  Debian: sudo apt install python3.13 python3.13-venv
  pyenv:  pyenv install 3.13"

if command -v uv >/dev/null 2>&1
then
  USE_UV=1
  dim "using uv (found in PATH)"
else
  USE_UV=0
  dim "uv not found — using the venv module + pip"
fi

tag=$(resolve_tag) || exit 1
url="https://github.com/$REPO/archive/refs/tags/$tag.tar.gz"
log "Installing corpora $tag"
dim "  venv:  $VENV"
dim "  bins:  $LOCAL_BIN"

# (Re)create the virtualenv — clear any existing one so a plain re-run
# upgrades in place instead of failing on the leftover venv.
if [ "$USE_UV" = 1 ]
then
  uv venv "$VENV" --python "$py" --clear >/dev/null
else
  [ ! -d "$VENV" ] || rm -rf "$VENV"
  "$py" -m venv "$VENV"
  # venv normally ships pip; ensure it's there for the fallback path.
  "$VENV/bin/python" -m pip --version >/dev/null 2>&1 ||
    "$VENV/bin/python" -m ensurepip --upgrade >/dev/null 2>&1 || true
fi
vp="$VENV/bin/python"

pip_install() { # <args…>
  if [ "$USE_UV" = 1 ]
  then
    uv pip install --python "$vp" "$@"
  else
    "$vp" -m pip install "$@"
  fi
}
pip_uninstall() { # <args…> — never fatal; these are optional strips
  if [ "$USE_UV" = 1 ]
  then
    uv pip uninstall --python "$vp" "$@" 2>/dev/null || true
  else
    "$vp" -m pip uninstall -y "$@" 2>/dev/null || true
  fi
}

pip_install "$url" || die "failed to install corpora from $url"
# Reinstall corpora-cli on top so its `corpora` script wins the entry-point
# clash with corpora-py < 2.3.0 (mirrors Formula/cli.rb:33-37).
pip_install --force-reinstall --no-deps "$url" || die "failed to fix the corpora entry point"
# Strip the REPL/serving extras the CLI never reaches (Formula/cli.rb:44-45).
pip_uninstall uvloop watchfiles jedi parso

# macOS: re-sign every native extension — pip's ad-hoc signatures are invalid
# and the kernel SIGKILLs them on Apple Silicon (mirrors the formula's
# post_install). Linux has no codesign and no signature enforcement.
if [ "$(uname)" = Darwin ]
then
  if command -v codesign >/dev/null 2>&1
  then
    dim "re-signing native extensions (macOS)"
    find "$VENV/lib" -type f \( -name '*.so' -o -name '*.dylib' \) \
      -exec codesign --force --sign - {} + || true
  else
    warn "codesign not found; native extensions may crash on Apple Silicon"
  fi
fi

# Link the three commands.
miss=0
for b in corpora corpora-api cf-mcp
do
  if [ ! -x "$VENV/bin/$b" ]
  then
    warn "$b was not installed (the corpora-py version may not ship it)"
    miss=1
  else
    link_bin "$b"
  fi
done

printf '\n' >&2
log "corpora $tag installed."
[ "$miss" = 1 ] || dim "  verify:  corpora --help"
case ":$PATH:" in
  *":$LOCAL_BIN:"*) ;;
  *)
    warn "$LOCAL_BIN is not on your PATH. Add it:"
    printf "  echo \"export PATH=%s:\$PATH\" >> ~/.bashrc  # or ~/.zshrc\n" "$LOCAL_BIN" >&2
    ;;
esac
