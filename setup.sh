#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
RELEASE_REPO="math-ai-org/mathcode"
RELEASE_TAG="v0.0.3"
LOCAL_ELAN_HOME="$ROOT_DIR/.local/elan"
LOCAL_ELAN_BIN="$LOCAL_ELAN_HOME/bin"
LEAN_WORKSPACE_DIR="$ROOT_DIR/lean-workspace"
MATHLIB_CACHE_MIN_KB=$((8 * 1024 * 1024))

cd "$ROOT_DIR"

add_to_path() {
  local dir="$1"
  [[ -d "$dir" ]] || return 0
  case ":$PATH:" in
    *":$dir:"*) ;;
    *) export PATH="$dir:$PATH" ;;
  esac
}

have_command() {
  command -v "$1" &>/dev/null
}

log() {
  printf '%s\n' "$1"
}

available_kb() {
  local path="$1"
  df -Pk "$path" | awk 'NR==2 { print $4 }'
}

# ── CLI flags ──────────────────────────────────────────────────────────────────

show_help() {
  cat <<'EOF'
Usage: bash setup.sh [OPTIONS]

Set up MathCode by downloading the release binary, bootstrapping the
Lean toolchain, and fetching the Mathlib cache.

Options:
  --help     Show this help message and exit
  --clean    Remove install artifacts (binary, venv, Lean toolchain,
             .env, caches) and exit. User outputs are kept.
  --status   Show what is currently installed and exit

Environment variables:
  MATHCODE_SKIP_MATHLIB_CACHE=1   Skip the ~8 GB Mathlib cache download
  MATHCODE_USE_LSP=1              Install LSP dependencies (default in .env)

Examples:
  bash setup.sh            # full install
  bash setup.sh --status   # check installation state
  bash setup.sh --clean    # remove install artifacts, keep proofs/vaults
EOF
}

do_clean() {
  log "Cleaning MathCode install artifacts from $ROOT_DIR ..."
  rm -f  "$ROOT_DIR/mathcode"
  rm -rf "$ROOT_DIR/AUTOLEAN"  # legacy: remove old Python AUTOLEAN if upgrading
  rm -rf "$LOCAL_ELAN_HOME"
  rm -f  "$ROOT_DIR/.env"
  # lean-workspace build artifacts (keep the source lakefile.toml + lean-toolchain)
  rm -rf "$LEAN_WORKSPACE_DIR/.lake"
  rm -rf "$LEAN_WORKSPACE_DIR/lake-packages"
  rm -rf "$LEAN_WORKSPACE_DIR/build"
  log "Kept user outputs in LeanFormalizations/ and ObsidianVault/."
  log "Done. Run 'bash setup.sh' to reinstall."
}

do_status() {
  log "MathCode installation status ($ROOT_DIR)"
  log "──────────────────────────────────────────"
  if [[ -x "$ROOT_DIR/mathcode" ]]; then
    local binary_version
    if binary_version="$("$ROOT_DIR/mathcode" --version 2>/dev/null)"; then
      log "Binary:       installed ($binary_version)"
    else
      log "Binary:       present but broken"
    fi
  else
    log "Binary:       not installed"
  fi
  if { have_command lean && have_command lake; } || [[ -x "$LOCAL_ELAN_BIN/lean" && -x "$LOCAL_ELAN_BIN/lake" ]]; then
    log "Lean:         installed"
  elif have_command lean || have_command lake || [[ -x "$LOCAL_ELAN_BIN/lean" ]] || [[ -x "$LOCAL_ELAN_BIN/lake" ]]; then
    log "Lean:         incomplete (need both lean and lake)"
  else
    log "Lean:         not installed"
  fi
  if [[ -f "$ROOT_DIR/.env" ]]; then
    log ".env:         present"
  else
    log ".env:         not created"
  fi
  local free_kb
  free_kb="$(available_kb "$ROOT_DIR" 2>/dev/null || echo 'unknown')"
  if [[ "$free_kb" != "unknown" ]]; then
    log "Disk free:    $(( free_kb / 1024 )) MB"
  fi
}

for arg in "$@"; do
  case "$arg" in
    --help|-h)
      show_help
      exit 0
      ;;
    --clean)
      do_clean
      exit 0
      ;;
    --status)
      do_status
      exit 0
      ;;
    *)
      log "Unknown option: $arg"
      log "Run 'bash setup.sh --help' for usage."
      exit 1
      ;;
  esac
done

shell_quote() {
  printf "'%s'" "${1//\'/\'\"\'\"\'}"
}

normalize_os() {
  case "$(uname -s)" in
    Darwin) printf 'darwin\n' ;;
    Linux) printf 'linux\n' ;;
    *)
      log "Unsupported operating system for MathCode releases: $(uname -s)"
      exit 1
      ;;
  esac
}

normalize_arch() {
  case "$(uname -m)" in
    arm64|aarch64) printf 'arm64\n' ;;
    x86_64|amd64) printf 'x86_64\n' ;;
    *)
      log "Unsupported CPU architecture for MathCode releases: $(uname -m)"
      exit 1
      ;;
  esac
}

release_archive_name() {
  printf 'mathcode-%s-%s-%s.tar.gz\n' "$RELEASE_TAG" "$(normalize_os)" "$(normalize_arch)"
}

release_bundle_name() {
  printf 'mathcode-%s-%s-%s\n' "$RELEASE_TAG" "$(normalize_os)" "$(normalize_arch)"
}

download_release_file() {
  local filename="$1"
  local output_path="$2"
  local url="https://github.com/$RELEASE_REPO/releases/download/$RELEASE_TAG/$filename"

  if ! have_command curl; then
    log "curl is required to download MathCode release assets."
    exit 1
  fi

  if ! curl -fL --retry 3 --retry-delay 1 -o "$output_path" "$url"; then
    log "Failed to download $url"
    log ""
    log "Check network connectivity and confirm the asset exists at:"
    log "  https://github.com/$RELEASE_REPO/releases/tag/$RELEASE_TAG"
    return 1
  fi
}

verify_release_archive() {
  local temp_dir="$1"

  if have_command shasum; then
    (
      cd "$temp_dir"
      LC_ALL=C LANG=C shasum -a 256 -c SHA256SUMS.txt
    )
    return 0
  fi

  if have_command sha256sum; then
    (
      cd "$temp_dir"
      sha256sum -c SHA256SUMS.txt
    )
    return 0
  fi

  log "Warning: shasum/sha256sum not found; skipping release checksum verification."
}

ensure_mathcode_binary() {
  local binary_path="$ROOT_DIR/mathcode"

  if [[ -x "$binary_path" ]] && "$binary_path" --version >/dev/null 2>&1; then
    return
  fi

  local archive_name bundle_name temp_dir archive_path checksum_path
  archive_name="$(release_archive_name)"
  bundle_name="$(release_bundle_name)"
  temp_dir="$(mktemp -d "${TMPDIR:-/tmp}/mathcode-bootstrap.XXXXXX")"
  archive_path="$temp_dir/$archive_name"
  checksum_path="$temp_dir/SHA256SUMS.txt"

  log "Downloading MathCode release from GitHub Releases ($RELEASE_TAG, $(normalize_os)/$(normalize_arch))"
  if ! download_release_file "$archive_name" "$archive_path"; then
    if [[ "$(normalize_os)" == "linux" ]]; then
      log "Linux support is tracked in: https://github.com/$RELEASE_REPO/issues/6"
    fi
    rm -rf "$temp_dir"
    exit 1
  fi
  if ! download_release_file "SHA256SUMS.txt" "$checksum_path"; then
    rm -rf "$temp_dir"
    exit 1
  fi
  if ! verify_release_archive "$temp_dir"; then
    rm -rf "$temp_dir"
    exit 1
  fi

  rm -f "$binary_path"
  LC_ALL=C LANG=C tar -xzf "$archive_path" \
    -C "$ROOT_DIR" \
    --strip-components=1 \
    "$bundle_name/mathcode"
  chmod +x "$binary_path"

  if ! "$binary_path" --version >/dev/null 2>&1; then
    rm -rf "$temp_dir"
    log "Downloaded MathCode binary did not start successfully."
    exit 1
  fi

  rm -rf "$temp_dir"
  log "Installed MathCode binary"
}

ensure_env_file() {
  local quoted_mathcode_path

  if [[ -f "$ROOT_DIR/.env" ]]; then
    return
  fi

  quoted_mathcode_path="$(shell_quote "$ROOT_DIR/mathcode")"

  cp "$ROOT_DIR/.env.example" "$ROOT_DIR/.env"

  cat >> "$ROOT_DIR/.env" <<PATHS

# Binary distribution paths (auto-generated by setup.sh)
LEAN_PROJECT_DIR="$ROOT_DIR/lean-workspace"
MATHCODE_CLI_CMD="$quoted_mathcode_path -p"

# Lean server (persistent REPL for sub-second compile checks)
MATHCODE_LEAN_REPL=1

# LSP integration (lemma search + structured diagnostics)
MATHCODE_USE_LSP=1

# Agent-mode proving
MATHCODE_AGENT_PROVE=1
PATHS

  log "Created .env with Lean server + LSP enabled by default."
}

ensure_lean() {
  if have_command lean && have_command lake; then
    log "Using existing Lean: $(command -v lean)"
    log "Using existing Lake: $(command -v lake)"
    return
  fi

  export ELAN_HOME="$LOCAL_ELAN_HOME"
  add_to_path "$LOCAL_ELAN_BIN"

  if [[ ! -x "$LOCAL_ELAN_BIN/elan" ]]; then
    if [[ ! -f "$LEAN_WORKSPACE_DIR/lean-toolchain" ]]; then
      log "Bundled Lean workspace is missing: $LEAN_WORKSPACE_DIR"
      exit 1
    fi

    local lean_toolchain
    lean_toolchain="$(<"$LEAN_WORKSPACE_DIR/lean-toolchain")"

    case "$OSTYPE" in
      darwin*|linux*)
        log "Installing Lean locally into .local/elan"
        curl -sSf https://raw.githubusercontent.com/leanprover/elan/master/elan-init.sh | env ELAN_HOME="$LOCAL_ELAN_HOME" sh -s -- -y --default-toolchain "$lean_toolchain" --no-modify-path
        ;;
      *)
        log "Lean is not installed and this script only auto-installs Lean locally on macOS/Linux."
        log "Install elan manually, then rerun ./setup.sh."
        exit 1
        ;;
    esac
  fi

  add_to_path "$LOCAL_ELAN_BIN"

  if ! have_command lean || ! have_command lake; then
    log "Lean installation did not expose lean and lake on PATH."
    exit 1
  fi

  log "Using Lean: $(command -v lean)"
  log "Using Lake: $(command -v lake)"
}

bootstrap_lean_workspace() {
  if [[ ! -f "$LEAN_WORKSPACE_DIR/lakefile.toml" ]]; then
    log "Bundled Lean workspace is missing: $LEAN_WORKSPACE_DIR"
    exit 1
  fi

  local skip_cache=0
  local free_kb

  if [[ "${MATHCODE_SKIP_MATHLIB_CACHE:-0}" == "1" ]]; then
    skip_cache=1
    log "Skipping Mathlib cache download because MATHCODE_SKIP_MATHLIB_CACHE=1."
  else
    free_kb="$(available_kb "$LEAN_WORKSPACE_DIR")"
    if [[ -n "$free_kb" && "$free_kb" -lt "$MATHLIB_CACHE_MIN_KB" ]]; then
      skip_cache=1
      log "Skipping 'lake exe cache get' because disk space is low (${free_kb}KB free)."
      log "Free up more disk space and rerun setup later if you want the bundled Mathlib cache."
    fi
  fi

  log "Bootstrapping bundled Lean workspace"
  (
    cd "$LEAN_WORKSPACE_DIR"
    MATHLIB_NO_CACHE_ON_UPDATE=1 lake update
  )

  if [[ "$skip_cache" -eq 1 ]]; then
    return
  fi

  log "Fetching Mathlib cache (best effort)"
  set +e
  (
    cd "$LEAN_WORKSPACE_DIR"
    lake exe cache get
  )
  local cache_status=$?
  set -e

  if [[ "$cache_status" -ne 0 ]]; then
    log "Warning: 'lake exe cache get' failed. The first Mathlib compile may take longer."
  fi
}

ensure_mathcode_binary
ensure_env_file
ensure_lean
bootstrap_lean_workspace

log "Release setup complete."
log "Run: ./run"
