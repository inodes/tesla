#!/bin/bash
# Makes every Python/shell script in the repo user-executable (u+x), without
# touching anything else. Skips virtualenvs and caches. Safe to run any time.
#   ./Tools/fix_exec_bits.sh          # fix and list what changed
#   ./Tools/fix_exec_bits.sh --check  # list only, exit 1 if any are missing u+x
#   ./Tools/fix_exec_bits.sh --install-hook  # (re)create .git/hooks/pre-commit (hooks are not versioned)
set -u
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
if [ "${1:-}" = "--install-hook" ]; then
  cat > "$ROOT/.git/hooks/pre-commit" <<'HOOK'
#!/bin/bash
# Every staged .py/.sh must be user-executable, and git must record that.
root="$(git rev-parse --show-toplevel)"
git diff --cached --name-only --diff-filter=AM -- '*.py' '*.sh' | while IFS= read -r f; do
  [ -f "$root/$f" ] || continue
  chmod u+x "$root/$f"
  git update-index --chmod=+x -- "$f"
done
exit 0
HOOK
  chmod u+x "$ROOT/.git/hooks/pre-commit" && echo "installed .git/hooks/pre-commit"
  exit 0
fi
MODE=fix; [ "${1:-}" = "--check" ] && MODE=check
missing=0
while IFS= read -r f; do
  if [ ! -x "$f" ]; then
    missing=$((missing + 1))
    if [ "$MODE" = fix ]; then chmod u+x "$f" && echo "chmod u+x ${f#$ROOT/}"; else echo "missing u+x: ${f#$ROOT/}"; fi
  fi
done < <(find "$ROOT" \( -name .git -o -name .direnv -o -name .venv -o -name venv -o -name node_modules -o -name __pycache__ \) -prune \
         -o -type f \( -name '*.py' -o -name '*.sh' \) -print)
[ "$MODE" = check ] && [ "$missing" -gt 0 ] && exit 1
exit 0
