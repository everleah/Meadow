#!/usr/bin/env bash
# Deliberate AI-slop scan for Meadow PIM - rationale in plan/detail/ai-code-quality.md.
#
#   bash scripts/deslop.sh                  # whole repository (baseline applied)
#   bash scripts/deslop.sh staged           # exactly what is about to be committed
#   bash scripts/deslop.sh changed          # staged + unstaged changes against HEAD
#   bash scripts/deslop.sh all --strict     # complete current state, baseline ignored
#   bash scripts/deslop.sh changed --fix-plan
#
# This is a quality signal, not a verdict. It never replaces scripts/check.sh, it
# never proves runtime correctness, and it never rewrites code on its own: the
# Python deep dive only reports. Repairs stay deliberate, one category at a time,
# with the focused tests re-run afterwards.
set -euo pipefail

cd "$(dirname "$0")/.."

stopslop_version="0.5.1"
slop_detector_version="4.0"
report_dir=".tmp/deslop"

selection="all"
strict=0
fix_plan=0

for argument in "$@"; do
    case "$argument" in
        all|changed|staged) selection="$argument" ;;
        --strict) strict=1 ;;
        --fix-plan) fix_plan=1 ;;
        *)
            printf 'Unknown argument: %s (use all|changed|staged, --strict, --fix-plan)\n' "$argument" >&2
            exit 2
            ;;
    esac
done

step() { printf '\n\033[1m==> %s\033[0m\n' "$1"; }

if ! command -v stopslop >/dev/null 2>&1; then
    printf 'stopslop is missing. Install the pinned version:\n' >&2
    printf '  cargo install stopslop --version %s --locked\n' "$stopslop_version" >&2
    exit 2
fi

installed="$(stopslop --version | awk '{print $NF}')"
if [ "$installed" != "$stopslop_version" ]; then
    printf 'warning: stopslop %s is installed, the repository pins %s\n' \
        "$installed" "$stopslop_version" >&2
fi

# stopslop 0.5.1 has no git-aware selection (`--staged`/`--changed` are documented
# upstream but not in this release), so the file list comes from git. An empty
# selection exits clean instead of scanning the whole tree. As upstream, untracked
# files are not part of `changed`.
git_file_list() {
    if [ "$1" = "staged" ]; then
        git diff --cached --name-only --diff-filter=ACMR
    else
        git diff --name-only --diff-filter=ACMR HEAD
    fi
}

scan=()
if [ "$selection" = "all" ]; then
    scan=(".")
else
    while IFS= read -r path; do
        if [ -n "$path" ]; then
            scan+=("$path")
        fi
    done < <(git_file_list "$selection")
fi

if [ "${#scan[@]}" -eq 0 ]; then
    printf 'Nothing to scan for selection "%s".\n' "$selection"
    exit 0
fi

mkdir -p "$report_dir"
printf '{"accepted": {}}\n' > "$report_dir/empty-baseline.json"

# The baseline keeps the accepted findings of the repository out of the way;
# `--strict` shows the complete current state instead.
run_scan() {
    if [ "$strict" -eq 1 ]; then
        stopslop "${scan[@]}" --baseline="$report_dir/empty-baseline.json" "$@"
    else
        stopslop "${scan[@]}" "$@"
    fi
}

set +e
run_scan --format json > "$report_dir/stopslop-$selection.json"
json_status=$?
step "stopslop ($selection)"
printf 'report: %s\n' "$report_dir/stopslop-$selection.json"
run_scan
text_status=$?
set -e

status=$json_status
if [ "$text_status" -gt "$status" ]; then
    status=$text_status
fi

if [ "$fix_plan" -eq 1 ]; then
    step "Python deep dive (ai-slop-detector ${slop_detector_version}, report only)"
    if command -v uv >/dev/null 2>&1; then
        # The Python we ship plus our tooling. `tests` is excluded by the tool
        # itself, and scratch files under `.tmp/` are not part of the project, so
        # they must not show up in a quality report.
        for deep_path in meadow scripts; do
            uv run slop-detector "$deep_path" --no-color || true
            uv run slop-detector "$deep_path" --json \
                --output "$report_dir/slop-detector-$deep_path.json" >/dev/null || true
            printf 'report: %s\n' "$report_dir/slop-detector-$deep_path.json"
        done
    else
        printf 'uv is missing, so the Python deep dive is skipped (uv sync, then retry)\n' >&2
    fi
    printf 'Repairs are deliberate: one category, focused tests, then scripts/check.sh python.\n'
fi

if [ "$status" -ne 0 ]; then
    printf '\nFindings were reported. Investigate before suppressing, and repair per\n'
    printf 'category - details in plan/detail/ai-code-quality.md.\n' >&2
fi

exit "$status"
