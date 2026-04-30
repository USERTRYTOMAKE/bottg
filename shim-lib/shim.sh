# Cowork plugin shim library.
#
# The host stages this from desktop/resources into a ro mount at
# /sessions/<vm>/mnt/.cowork-lib/shim.sh. Plugin shims discover it by
# walking up from their own BASH_SOURCE to mnt/ — no env var, so the
# agent cannot redirect to a fake library.
#
# A plugin shim using this library is ~8 lines:
#
#   #!/bin/bash
#   set -euo pipefail
#   _mnt="${BASH_SOURCE[0]}"
#   while [[ "$_mnt" != "/" && "$(basename "$_mnt")" != "mnt" ]]; do
#     _mnt="$(dirname "$_mnt")"
#   done
#   source "$_mnt/.cowork-lib/shim.sh"
#   cowork_require_token GOOGLE_WORKSPACE_CLI_TOKEN
#   cowork_gate "$@"
#   cowork_exec gws "$@"
#
# Argv classification, the confirm protocol, and arch dispatch are all
# driven by .claude-plugin/plugin.json — plugin authors declare which
# argv patterns need confirmation; they don't write bash for it.

# Caller's bin/<cli> → plugin root. ${BASH_SOURCE[1]} is the sourcing shim.
_cowork_plugin_root="$(cd "$(dirname "${BASH_SOURCE[1]}")/.." && pwd)"
_cowork_manifest="$_cowork_plugin_root/.claude-plugin/plugin.json"
_cowork_plugin_name="$(jq -r '.name' "$_cowork_manifest")"
_cowork_cli="$(basename "${BASH_SOURCE[1]}")"

# --- cowork_require_token ENV_VAR ------------------------------------------
# Fails closed with a "not connected" message if the token env var is unset.
cowork_require_token() {
  local var="$1"
  if [[ -z "${!var:-}" ]]; then
    echo "$_cowork_plugin_name: not connected. Open Claude settings → Plugins → $_cowork_plugin_name → Connect." >&2
    exit 2
  fi
}

# --- cowork_gate ARGV... ---------------------------------------------------
# Classifies argv against the manifest's `confirm` rules. For ops that
# match, blocks on the renderer permission bridge before returning.
#
# Manifest schema (plugin.json → .confirm):
#   [
#     { "op": "send_email", "match": "gmail +send" },
#     { "op": "send_invites", "match": "calendar +insert", "flag": "--attendee" },
#     { "op": "publish_release", "match": "release create", "unless_flag": "--draft" }
#   ]
#
# Rules are checked in order; first match wins. Matching is token-wise
# against the argv array (jq $ARGS.positional — no space-joining):
#
#   `match`        Tokens appear as a contiguous subsequence anywhere in
#                  argv. `pr merge` matches `-R o/r pr merge 123` but not
#                  `pr view`.
#   `flag`         Flag is present in any of three forms: the token
#                  sequence as-is (`--method DELETE`), the tokens joined
#                  with `=` as a single element (`--method=DELETE`), or
#                  for single-token flags, the flag as a prefix followed
#                  by `=` (`--attendee` matches `--attendee=foo@bar`).
#   `unless_flag`  Stricter: exact token sequence only (no `=` forms).
#                  Lenient matching here would let `--draft=false` skip
#                  the gate, so the false positive (an extra prompt when
#                  `--draft=true` is passed) is the safe direction.
#
# Per-token equality is exact, so `--title=--draft` does not satisfy
# `unless_flag: "--draft"`.
cowork_gate() {
  local op
  op="$(
    jq -r '
      def contig($n):
        . as $h | ($n|length) as $k
        | $k == 0 or any(range(0; ($h|length)-$k+1); $h[.:.+$k] == $n);
      def flag_present($f):
        ($f|split(" ")) as $p | . as $args
        | ($args | contig($p))
          or ($args | any(. == ($p|join("="))))
          or (($p|length) == 1 and ($args | any(startswith($p[0] + "="))));
      $ARGS.positional as $args
      | first(
          (.confirm // [])[]
          | (.match|split(" ")) as $m | select($args | contig($m))
          | .flag        as $f | select(if $f then  $args | flag_present($f)        else true end)
          | .unless_flag as $u | select(if $u then ($args | contig($u|split(" ")) | not) else true end)
          | .op
        ) // ""
    ' "$_cowork_manifest" --args -- "$@"
  )"

  [[ -z "$op" ]] && return 0  # ungated — routine or read

  # Derive the permission channel paths from this library's own location.
  # The library is at .../mnt/.cowork-lib/shim.sh (ro mount); the request
  # and response dirs are sibling mounts under the same mnt/. No env var,
  # so the agent cannot redirect to a fake directory.
  local mnt; mnt="$(dirname "$(dirname "${BASH_SOURCE[0]}")")"
  local req_dir="$mnt/.cowork-perm-req"
  local resp_dir="$mnt/.cowork-perm-resp"
  if [[ ! -d "$req_dir" || ! -d "$resp_dir" ]]; then
    echo "$_cowork_plugin_name: operation '$op' requires confirmation but the permission bridge is not available." >&2
    exit 2
  fi
  # Deterministic nonce — hash of plugin+op+argv. If the shim times out
  # below and the agent retries with identical argv, it reuses the same
  # slot: the pending card on the host stays bound to the same nonce,
  # and a late user click still lands in the right response file.
  local argv="$*"
  local nonce
  nonce="$(printf '%s\0%s\0%s' "$_cowork_plugin_name" "$op" "$argv" | sha256sum | cut -c1-32)"
  local req="$req_dir/$nonce"
  local resp="$resp_dir/$nonce"

  # Fast path: a response may already exist from a previous invocation
  # that timed out while the permission card was still pending. Consume
  # it now without re-requesting.
  if [[ ! -f "$resp" ]]; then
    # Atomic publish — write to a dotfile (ignored by the host's watcher)
    # and rename so the fs.watch event fires on a complete file.
    jq -n --arg plugin "$_cowork_plugin_name" --arg op "$op" --arg argv "$argv" \
      '{plugin:$plugin, op:$op, argv:$argv}' > "$req_dir/.$nonce.tmp"
    mv "$req_dir/.$nonce.tmp" "$req"

    # Poll for ~100s (under the Bash tool's 120s default). If the
    # user hasn't decided by then, exit with a retry hint.
    local waited=0
    while [[ ! -f "$resp" ]]; do
      if (( waited >= 1000 )); then
        echo "$_cowork_plugin_name: operation '$op' is waiting on human confirmation." >&2
        echo "  The permission prompt might still be open or may have disappeared if too much time has passed. Instruct the user to try again and answer the permission prompt." >&2
        exit 75  # EX_TEMPFAIL
      fi
      sleep 0.1; waited=$((waited + 1))
    done
  fi

  local decision; read -r decision < "$resp"
  # Both req and resp are cleaned up host-side (respond() unlinks the
  # request; a 5s timer sweeps the response). The VM mount denies
  # delete even in rw mode, so a shim-side rm -f would hit EACCES —
  # which -f does not suppress — and set -e would kill us here.
  [[ "$decision" == "allow" ]] && return 0
  echo "$_cowork_plugin_name: operation '$op' denied by user" >&2
  exit 2
}

# --- cowork_exec BINARY_PREFIX ARGV... -------------------------------------
# Picks the right arch-tagged binary from bin/ and execs it. Binaries are
# expected at bin/<prefix>-<arch>-linux* (gitignore pattern */bin/*-linux*).
cowork_exec() {
  local prefix="$1"; shift
  local bin_dir="$_cowork_plugin_root/bin"
  local arch
  case "$(uname -m)" in
    aarch64|arm64) arch="aarch64" ;;
    x86_64|amd64)  arch="x86_64"  ;;
    *) echo "$_cowork_plugin_name: unsupported architecture '$(uname -m)'" >&2; exit 1 ;;
  esac
  # Glob the middle+suffix (-linux, -unknown-linux-musl, -linux-gnu, ...).
  local bin
  for bin in "$bin_dir/$prefix-$arch"*linux*; do
    [[ -x "$bin" ]] && exec "$bin" "$@"
  done
  echo "$_cowork_plugin_name: no executable binary found matching $bin_dir/$prefix-$arch-linux*" >&2
  exit 1
}
