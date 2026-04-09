#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SETUP_SH="$ROOT_DIR/setup.sh"

fail() {
  printf 'FAIL: %s\n' "$1" >&2
  exit 1
}

assert_contains() {
  local haystack="$1"
  local needle="$2"
  case "$haystack" in
    *"$needle"*) ;;
    *) fail "expected output to contain: $needle" ;;
  esac
}

assert_not_contains() {
  local haystack="$1"
  local needle="$2"
  case "$haystack" in
    *"$needle"*) fail "expected output to omit: $needle" ;;
    *) ;;
  esac
}

make_temp_dir() {
  mktemp -d "${TMPDIR:-/tmp}/mathcode-setup-tests.XXXXXX"
}

test_help_text() {
  local output
  output="$(bash "$SETUP_SH" --help)"
  assert_contains "$output" "User outputs are kept."
  assert_contains "$output" "remove install artifacts, keep proofs/vaults"
}

test_clean_preserves_user_outputs() {
  local tmpdir output
  tmpdir="$(make_temp_dir)"

  cp "$SETUP_SH" "$tmpdir/setup.sh"
  touch "$tmpdir/mathcode" "$tmpdir/.env"
  mkdir -p "$tmpdir/AUTOLEAN" \
    "$tmpdir/.local/elan" \
    "$tmpdir/lean-workspace/.lake" \
    "$tmpdir/lean-workspace/lake-packages" \
    "$tmpdir/lean-workspace/build" \
    "$tmpdir/LeanFormalizations" \
    "$tmpdir/ObsidianVault"

  output="$(bash "$tmpdir/setup.sh" --clean)"

  [[ ! -e "$tmpdir/mathcode" ]] || fail "--clean should remove mathcode"
  [[ ! -e "$tmpdir/.env" ]] || fail "--clean should remove .env"
  [[ ! -d "$tmpdir/AUTOLEAN" ]] || fail "--clean should remove AUTOLEAN"
  [[ ! -d "$tmpdir/.local/elan" ]] || fail "--clean should remove local elan"
  [[ ! -d "$tmpdir/lean-workspace/.lake" ]] || fail "--clean should remove .lake"
  [[ ! -d "$tmpdir/lean-workspace/lake-packages" ]] || fail "--clean should remove lake-packages"
  [[ ! -d "$tmpdir/lean-workspace/build" ]] || fail "--clean should remove build"
  [[ -d "$tmpdir/LeanFormalizations" ]] || fail "--clean should preserve LeanFormalizations"
  [[ -d "$tmpdir/ObsidianVault" ]] || fail "--clean should preserve ObsidianVault"
  assert_contains "$output" "Kept user outputs in LeanFormalizations/ and ObsidianVault/."

  rm -rf "$tmpdir"
}

test_status_marks_broken_and_incomplete_installs() {
  local tmpdir output
  tmpdir="$(make_temp_dir)"

  cp "$SETUP_SH" "$tmpdir/setup.sh"
  cat >"$tmpdir/mathcode" <<'EOF'
#!/usr/bin/env bash
exit 1
EOF
  chmod +x "$tmpdir/mathcode"

  mkdir -p "$tmpdir/.local/elan/bin"
  cat >"$tmpdir/.local/elan/bin/lean" <<'EOF'
#!/usr/bin/env bash
exit 0
EOF
  chmod +x "$tmpdir/.local/elan/bin/lean"

  output="$(PATH="/usr/bin:/bin" bash "$tmpdir/setup.sh" --status)"

  assert_contains "$output" "Binary:       present but broken"
  assert_contains "$output" "Lean:         incomplete (need both lean and lake)"

  rm -rf "$tmpdir"
}

test_download_failure_message_stays_generic() {
  local tmpdir output
  tmpdir="$(make_temp_dir)"

  cp "$SETUP_SH" "$tmpdir/setup.sh"
  mkdir -p "$tmpdir/fakebin"
  cat >"$tmpdir/fakebin/curl" <<'EOF'
#!/usr/bin/env bash
exit 1
EOF
  chmod +x "$tmpdir/fakebin/curl"

  if output="$(PATH="$tmpdir/fakebin:/usr/bin:/bin" bash "$tmpdir/setup.sh" 2>&1)"; then
    fail "setup.sh should fail when curl cannot download release assets"
  fi

  assert_contains "$output" "Check network connectivity and confirm the asset exists at:"
  assert_not_contains "$output" "does not include a binary"

  rm -rf "$tmpdir"
}

bash -n "$SETUP_SH"
test_help_text
test_clean_preserves_user_outputs
test_status_marks_broken_and_incomplete_installs
test_download_failure_message_stays_generic

printf 'setup flag tests passed\n'
