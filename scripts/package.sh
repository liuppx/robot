#!/usr/bin/env bash
set -euo pipefail

root_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
project_name="${PROJECT_NAME:-$(basename "$root_dir")}"
out_dir="$root_dir/output"
remote_name="${PACKAGE_REMOTE:-origin}"
auto_build="${AUTO_BUILD:-true}"
tag_arg="${1:-}"
source_dir=""
worktree_dir=""

binary_rel="dashboard/target/release/bot-hub-control-plane"
web_rel="dashboard/web"
env_template_rel="config/bot-hub.env.template"
starter_rel="scripts/starter.sh"

usage() {
  echo "Usage: $(basename "$0") [v<major>.<minor>.<patch>]" >&2
}

require_cmd() {
  local cmd="$1"
  if ! command -v "$cmd" >/dev/null 2>&1; then
    echo "Missing command in PATH: $cmd" >&2
    exit 1
  fi
}

is_semver_tag() {
  [[ "$1" =~ ^v[0-9]+\.[0-9]+\.[0-9]+$ ]]
}

extract_max_tag() {
  git -C "$root_dir" tag -l 'v[0-9]*.[0-9]*.[0-9]*' | sort -V | tail -n 1
}

increment_patch_tag() {
  local tag="$1"
  local major minor patch
  major="${tag#v}"
  major="${major%%.*}"
  minor="${tag#v${major}.}"
  minor="${minor%%.*}"
  patch="${tag##*.}"
  echo "v${major}.${minor}.$((patch + 1))"
}

ensure_remote_exists() {
  if ! git -C "$root_dir" remote get-url "$remote_name" >/dev/null 2>&1; then
    echo "Remote not found: $remote_name" >&2
    exit 1
  fi
}

fetch_remote_refs() {
  ensure_remote_exists
  echo "Fetching latest refs from remote '$remote_name'..."
  git -C "$root_dir" fetch "$remote_name" --prune --tags
}

ensure_cargo() {
  if command -v cargo >/dev/null 2>&1; then
    return 0
  fi
  if [[ -f "$HOME/.cargo/env" ]]; then
    # shellcheck disable=SC1090
    source "$HOME/.cargo/env"
  fi
  if ! command -v cargo >/dev/null 2>&1; then
    echo "Missing cargo in PATH. Install Rust toolchain first." >&2
    exit 1
  fi
}

prepare_source_dir() {
  local ref="$1"
  worktree_dir="$(mktemp -d "${TMPDIR:-/tmp}/${project_name}-package.XXXXXX")"
  git -C "$root_dir" worktree add --detach "$worktree_dir" "$ref" >/dev/null
  source_dir="$worktree_dir"
}

build_artifacts() {
  if [[ "$auto_build" != "true" ]]; then
    return 0
  fi

  ensure_cargo
  echo "Building Rust control-plane binary (release)..."
  (cd "$source_dir/dashboard" && cargo build --release)
}

verify_artifacts() {
  local bin_src="$source_dir/$binary_rel"
  local web_src="$source_dir/$web_rel"
  local env_template_src="$source_dir/$env_template_rel"
  local starter_src="$source_dir/$starter_rel"

  if [[ ! -x "$bin_src" ]]; then
    echo "Missing release binary: $bin_src" >&2
    exit 1
  fi
  if [[ ! -d "$web_src" ]]; then
    echo "Missing frontend static directory: $web_src" >&2
    exit 1
  fi
  if [[ ! -f "$env_template_src" ]]; then
    echo "Missing env template: $env_template_src" >&2
    exit 1
  fi
  if [[ ! -f "$starter_src" ]]; then
    echo "Missing starter script: $starter_src" >&2
    exit 1
  fi
}

cleanup() {
  if [[ -n "$worktree_dir" && -d "$worktree_dir" ]]; then
    git -C "$root_dir" worktree remove --force "$worktree_dir" >/dev/null 2>&1 || rm -rf "$worktree_dir"
  fi
}
trap cleanup EXIT

require_cmd git
require_cmd tar

current_branch="$(git -C "$root_dir" rev-parse --abbrev-ref HEAD)"

build_ref=""
build_hash_full=""
target_tag=""

if [[ -n "$tag_arg" ]]; then
  if ! is_semver_tag "$tag_arg"; then
    usage
    exit 1
  fi

  fetch_remote_refs

  if ! git -C "$root_dir" rev-parse -q --verify "refs/tags/$tag_arg" >/dev/null; then
    echo "Tag not found, skip package: $tag_arg"
    exit 0
  fi

  target_tag="$tag_arg"
  build_ref="$target_tag"
  build_hash_full="$(git -C "$root_dir" rev-list -n 1 "$target_tag")"

  prepare_source_dir "$build_ref"
  build_artifacts
else
  fetch_remote_refs

  remote_main_ref="refs/remotes/$remote_name/main"
  if ! git -C "$root_dir" rev-parse -q --verify "$remote_main_ref" >/dev/null; then
    echo "Missing remote branch: $remote_name/main" >&2
    exit 1
  fi

  max_tag="$(extract_max_tag)"
  main_hash_full="$(git -C "$root_dir" rev-parse "$remote_main_ref")"

  max_tag_hash_full=""
  if [[ -n "$max_tag" ]]; then
    max_tag_hash_full="$(git -C "$root_dir" rev-list -n 1 "$max_tag")"
  fi

  if [[ -n "$max_tag_hash_full" && "$max_tag_hash_full" == "$main_hash_full" ]]; then
    echo "Latest tag $max_tag already matches $remote_name/main HEAD, skip package."
    exit 0
  fi

  if [[ -z "$max_tag" ]]; then
    target_tag="v0.0.1"
  else
    target_tag="$(increment_patch_tag "$max_tag")"
  fi

  if git -C "$root_dir" rev-parse -q --verify "refs/tags/$target_tag" >/dev/null; then
    echo "Tag already exists, refuse to overwrite: $target_tag" >&2
    exit 1
  fi

  build_ref="$main_hash_full"
  build_hash_full="$main_hash_full"

  prepare_source_dir "$build_ref"
  build_artifacts

  git -C "$root_dir" tag "$target_tag" "$main_hash_full"
  if ! git -C "$root_dir" push "$remote_name" "$target_tag"; then
    git -C "$root_dir" tag -d "$target_tag" >/dev/null 2>&1 || true
    echo "Failed to push tag to remote: $target_tag" >&2
    exit 1
  fi
fi

if [[ -z "$build_hash_full" ]]; then
  build_hash_full="$(git -C "$root_dir" rev-parse "$build_ref")"
fi

verify_artifacts

target_hash="$(git -C "$root_dir" rev-parse --short=7 "$build_hash_full")"
pkg_name="${project_name}-${target_tag}-${target_hash}"
stage_dir="$out_dir/$pkg_name"
archive_path="$out_dir/${pkg_name}.tar.gz"

mkdir -p "$out_dir"
rm -rf "$stage_dir"
mkdir -p "$stage_dir/build" "$stage_dir/config" "$stage_dir/scripts" "$stage_dir/dashboard"

cp "$source_dir/$binary_rel" "$stage_dir/build/bot-hub-control-plane"
cp "$source_dir/$env_template_rel" "$stage_dir/config/bot-hub.env.template"
cp "$source_dir/$starter_rel" "$stage_dir/scripts/starter.sh"
chmod +x "$stage_dir/scripts/starter.sh"
cp -R "$source_dir/$web_rel" "$stage_dir/dashboard/"

printf '%s\n' "$target_tag" > "$stage_dir/VERSION"
printf '%s\n' "$build_hash_full" > "$stage_dir/COMMIT"
printf '%s\n' "$current_branch" > "$stage_dir/BUILD_SOURCE_BRANCH"

rm -f "$archive_path"
tar -czf "$archive_path" -C "$out_dir" "$pkg_name"

if [[ "${KEEP_STAGE:-0}" != "1" ]]; then
  rm -rf "$stage_dir"
fi

echo "Package created: $archive_path"
