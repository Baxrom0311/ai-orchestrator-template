# Orchestration Steering — TMS Full Bug Fix

## Overview

Bug fix sprint for Laravel 12 TMS — backend logic bugs + frontend dark/light theme issues. 5 parallel builder agents.

## Agents

- **ai-planner** — Splits bugs into 5 parallel tracks, tracks progress
- **ai-builder** (×5) — Makes code changes to fix bugs
- **ai-reviewer** — Verifies fixes are correct, no regressions

## Loop Structure

```
Plan (split into 5 tracks) → [5× Build parallel → Test → Review] × N → Replan → Done
```

## Parallel Tracks

| Track | Agent | Focus |
|-------|-------|-------|
| A | builder-1 | Backend CRITICAL (BUG-1, BUG-2) |
| B | builder-2 | Backend HIGH (BUG-3, BUG-4, BUG-5) |
| C | builder-3 | Backend MEDIUM/LOW (BUG-6 to BUG-10) |
| D | builder-4 | Frontend core dark mode (app.blade.php, chat) |
| E | builder-5 | Frontend page-level theme fixes + discovery |

## Safety

- No git push (auto_push = false)
- No migration changes
- No vendor/node_modules changes
- No .env changes
- Tracks must NOT conflict on same files
