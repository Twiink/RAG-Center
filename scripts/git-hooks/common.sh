#!/usr/bin/env sh
# Git hooks 公共函数。

print_section() {
  printf '\n[check] %s\n' "$1"
}

print_skip() {
  printf '[skip] %s\n' "$1"
}

print_warn() {
  printf '[warn] %s\n' "$1" >&2
}

print_error() {
  printf '[error] %s\n' "$1" >&2
}

ensure_command() {
  if ! command -v "$1" >/dev/null 2>&1; then
    print_error "缺少命令：$1"
    return 1
  fi
}

current_git_branch() {
  git symbolic-ref --quiet --short HEAD 2>/dev/null || true
}

is_truthy() {
  normalized="$(printf '%s' "$1" | tr '[:upper:]' '[:lower:]')"
  case "$normalized" in
    1|true|yes|on) return 0 ;;
    *) return 1 ;;
  esac
}
