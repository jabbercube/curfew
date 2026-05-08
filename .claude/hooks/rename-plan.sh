#!/bin/sh
# Rename freshly-written plan files in .claude/plans/ to:
#   <YYYY-MM-DD>-<title-slug>.md
# Title-slug is derived from the first `# H1` line of the file. Falls back to
# the file's existing basename (Claude's auto-slug) if no H1 is found.
# On collision, append -2, -3, ... — never overwrite.

set -eu

cd "${CLAUDE_PROJECT_DIR:-.}/.claude/plans" 2>/dev/null || exit 0
d=$(date +%Y-%m-%d)

slugify_title() {
    awk '/^# / { sub(/^# +/, ""); print; exit }' "$1" \
        | tr '[:upper:]' '[:lower:]' \
        | LC_ALL=C tr -c 'a-z0-9\n' '-' \
        | sed -E 's/-+/-/g; s/^-+//; s/-+$//' \
        | cut -c 1-60 \
        | sed -E 's/-+$//'
}

next_free() {
    base=$1
    target="$base.md"
    if [ ! -e "$target" ]; then
        printf '%s\n' "$target"
        return
    fi
    i=2
    while [ -e "$base-$i.md" ]; do
        i=$((i + 1))
    done
    printf '%s\n' "$base-$i.md"
}

for f in *.md; do
    [ -e "$f" ] || continue
    case "$f" in
        [0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]-*) continue ;;
    esac

    slug=$(slugify_title "$f" || true)
    [ -n "$slug" ] || slug=${f%.md}

    target=$(next_free "$d-$slug")
    if [ "$target" != "$d-$slug.md" ]; then
        printf 'plans hook: collision on %s, renamed to %s\n' "$d-$slug.md" "$target" >&2
    fi
    mv -n "$f" "$target"
done
