# Role: TMS Deep Discovery Scanner

Previous fixes are done. Now do a DEEP scan for any remaining issues.

## Deep Scan Areas

### 1. Visual Regression Check
```bash
grep -rn "bg-white\|bg-light\|text-dark\|text-black" resources/views/ --include="*.blade.php"
grep -rn 'style="[^"]*background[^"]*white\|style="[^"]*color[^"]*black\|style="[^"]*background-color[^"]*#fff' resources/views/ --include="*.blade.php"
grep -rn 'class="[^"]*text-white' resources/views/ --include="*.blade.php"
```

### 2. PHP Error Potential
```bash
grep -rn "->where('status', [0-9]" app/ --include="*.php"
grep -rn '\$request->' app/Services/ --include="*.php" | grep -v '\$request?->\|\$this->request'
grep -rn "auth()->user()->" app/ --include="*.php" | grep -v "?->"
```

### 3. Blade Syntax Issues
```bash
grep -rn '@yield\|@section\|@extends' resources/views/ --include="*.blade.php" | head -20
grep -rn '{{.*\$.*}}' resources/views/ --include="*.blade.php" | grep -v '@\|--'
```

### 4. Missing Error Handling
```bash
grep -rn "->find(\|->first(" app/Services/ --include="*.php" | grep -v "findOrFail\|firstOrFail\|?->"
```

### 5. Chat Page (largest file — 104KB)
```bash
grep -n "bg-white\|text-dark\|style=\"background\|style=\"color" resources/views/chat/index.blade.php
```

## Project Brief

{{brief}}

## Repository Snapshot

{{repo_snapshot}}

## Test Output

{{test_output}}

## Output

```json
{
  "should_continue": true,
  "completeness": 90,
  "scan_results": {
    "frontend_issues": 0,
    "backend_issues": 0,
    "security_issues": 0
  },
  "issues": ["description"],
  "new_tasks": [
    {"task": "Fix X in file Y", "priority": "high", "file": "path"}
  ],
  "updated_plan": "Plan for remaining fixes",
  "next_review_cycles": 2,
  "next_build_iterations": 5
}
```

Set `should_continue: false` ONLY when ALL scans return clean results.
