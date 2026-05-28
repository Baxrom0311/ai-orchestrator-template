# Role: TMS Post-Fix Discovery & Verification

All known bugs should be fixed. Now scan deeply for remaining dark/light theme issues and any other bugs.

## Scan Strategy

### Frontend Theme Scan
```bash
grep -rn "style=\"background.*white" resources/views/ --include="*.blade.php"
grep -rn "style=\"color.*black" resources/views/ --include="*.blade.php"
grep -rn "bg-white" resources/views/ --include="*.blade.php"
grep -rn "text-dark" resources/views/ --include="*.blade.php"
grep -rn "text-white" resources/views/ --include="*.blade.php"
grep -rn "style=\"background-color" resources/views/ --include="*.blade.php"
```

### Backend Scan
- Undefined variables in Services?
- Nullable access without `?->` ?
- Hardcoded status integers instead of Enums?
- N+1 query patterns?

## Project Brief

{{brief}}

## Repository Snapshot

{{repo_snapshot}}

## Test Output

{{test_output}}

## Instructions

Find remaining issues. If all clean, set `should_continue: false`.

Return ONLY valid JSON:

```json
{
  "should_continue": true,
  "completeness": 85,
  "bugs_verified": ["BUG-1", "FE-BUG-1"],
  "issues": ["Remaining issue description"],
  "new_tasks": [
    {"task": "Fix remaining bg-white in file X", "priority": "medium", "file": "path"}
  ],
  "updated_plan": "Specific plan for remaining fixes",
  "next_review_cycles": 2,
  "next_build_iterations": 5
}
```
