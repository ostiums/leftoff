#!/usr/bin/env zsh
# Installs leftoff. Safe to re-run (also used by `leftoff update`).
#
#   curl -fsSL https://raw.githubusercontent.com/ostiums/leftoff/main/install.sh | zsh
#   curl -fsSL …/install.sh | zsh -s -- --no-autosync     # don't sync chats at Claude start
#
# Run through a pipe, it clones (or fast-forwards) the repo into ~/.local/share/leftoff and
# continues from there. Autosync is switched on only on the first install, so an update never
# turns it back on after `leftoff autosync off`.
set -euo pipefail

autosync=1
for arg in "$@"; do
  case $arg in
    --no-autosync) autosync=0 ;;
    *) echo "Unknown argument: $arg" >&2; exit 2 ;;
  esac
done

if ! command -v python3 >/dev/null; then
  echo "python3 is required (xcode-select --install or brew install python)" >&2
  exit 1
fi

share="$HOME/.local/share/leftoff"
repo=${LEFTOFF_REPO:-https://github.com/ostiums/leftoff.git}
legacy_share="$HOME/.local/share/codex-resume"  # the tool was called codex-resume before

if [[ ${0:t} == install.sh && -f ${0:A:h}/leftoff.py ]]; then
  here=${0:A:h}
  # `codex-resume update` pulls into the old clone and runs this script from there: move it.
  if [[ $here == ${legacy_share:A} && ! -e $share ]]; then
    mv "$legacy_share" "$share"
    here=$share
    [[ -z ${LEFTOFF_REPO:-} ]] && git -C "$here" remote set-url origin "$repo"
  fi
else
  here=$share
  if ! command -v git >/dev/null; then
    echo "git is required (xcode-select --install)" >&2
    exit 1
  fi
  if [[ -d $here/.git ]]; then
    git -C "$here" pull --ff-only -q
  else
    mkdir -p "${here:h}"
    git clone -q "$repo" "$here"
  fi
fi

bin_dir="$HOME/.local/bin"
commands_dir="${CLAUDE_CONFIG_DIR:-$HOME/.claude}/commands"
link="$bin_dir/leftoff"
legacy_link="$bin_dir/codex-resume"
first_install=0
[[ -e $link || -L $legacy_link ]] || first_install=1
[[ -L $legacy_link ]] && rm -f "$legacy_link"
rm -f "$commands_dir/codex-import.md"

chmod +x "$here/leftoff.py" "$here/install.sh"
mkdir -p "$bin_dir" "$commands_dir"
ln -sf "$here/leftoff.py" "$link"
# The leftoff plugin brings its own /leftoff and hook; a copy here would only duplicate them.
if "$link" autosync status 2>/dev/null | grep -q "leftoff plugin"; then
  rm -f "$commands_dir/leftoff.md"
else
  cp "$here/commands/leftoff.md" "$commands_dir/leftoff.md"
fi

if ! command -v fzf >/dev/null; then
  if command -v brew >/dev/null; then
    brew install fzf
  else
    echo "fzf not found and no brew — chats will be picked from a numbered list (you can install fzf later)"
  fi
fi

path_line='export PATH="$HOME/.local/bin:$PATH"'
if [[ ":$PATH:" != *":$bin_dir:"* ]] && ! grep -qxF "$path_line" "$HOME/.zshrc" 2>/dev/null; then
  echo "$path_line" >> "$HOME/.zshrc"
  echo "Added ~/.local/bin to PATH (~/.zshrc) — open a new terminal window"
fi

if (( first_install && autosync )); then
  "$link" autosync on  # also runs the first sync
elif "$link" autosync status | grep -q "is on"; then
  "$link" autosync on >/dev/null  # rewrites a hook left by an older version to the current command
fi

echo "Done: leftoff  +  /leftoff in Claude Code"
