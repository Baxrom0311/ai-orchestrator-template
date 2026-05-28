# Continue Bug Fixing

You were fixing TMS bugs and got interrupted. Continue from where you left off.

## Rules
1. Check which bugs are already fixed (read the files)
2. Find the next unfixed bug from your assigned track in PROJECT_BRIEF.md
3. Fix it completely
4. Run verification if PHP changed: `php artisan route:list --json`
5. If your track is done, scan for additional theme issues using grep

## Previous Output

{{previous_builder_output}}

## Plan

{{kiro_plan}}

## Reviewer Feedback

{{previous_feedback}}

## Repository Snapshot

{{repo_snapshot}}

## Brief

{{brief}}

## Output

```json
{
  "state": "needs_review | complete | blocked",
  "bug_fixed": "BUG-X or FE-BUG-X",
  "summary": "What you changed",
  "files_changed": ["path/to/file"],
  "verification": "Commands run and results",
  "next_suggested_task": "Next bug",
  "blockers": []
}
```
