# Agent Rules — TMS Full Bug Fix Sprint

All AI agents working on this project MUST follow these rules strictly.

## General Rules

1. **Bug fix ONLY.** No new features, no refactoring beyond the fix.
2. **Minimal changes.** Touch only what's needed to fix the bug.
3. **Run verification after PHP changes.** `php artisan route:list --json`
4. **Do NOT touch:** migrations, vendor, node_modules, .env, storage.
5. **Do NOT run:** `git push`, `git commit`, `php artisan migrate`.
6. **5 agents work in parallel** — avoid file conflicts between tracks.

## Backend Rules

- Use existing Enums instead of hardcoded integers
- Proper null-safe operators (`?->`, `??`)
- Keep method signatures compatible
- Maintain PSR-12 code style

## Frontend/Theme Rules

- Use CoreUI CSS variables for colors: `var(--cui-body-bg)`, `var(--cui-body-color)`, `var(--cui-input-bg)`
- Use theme-aware classes: `bg-body` (not `bg-white`), `text-body` (not `text-dark`)
- Dark mode overrides use: `[data-coreui-theme="dark"] .selector { ... }`
- Remove inline `style="background: white"` or `style="color: black"`
- All fixes must work in BOTH dark and light modes
- Only Track D agent touches `layouts/app.blade.php` `<style>` block

## Parallel Track Assignment

| Track | Focus | Files | Agent |
|-------|-------|-------|-------|
| A | Backend CRITICAL | `app/Models/User.php`, `app/Services/ProjectService.php` | builder-1 |
| B | Backend HIGH | `app/Services/DepartmentService.php`, `app/Services/TaskRejectService.php`, `app/Observers/`, `app/Services/TaskService.php` | builder-2 |
| C | Backend MEDIUM/LOW | `app/Models/Department.php`, `app/Models/User.php` (dashboard methods), `routes/api.php`, `app/Services/TaskService.php` | builder-3 |
| D | Frontend layout + dark mode core | `resources/views/layouts/app.blade.php`, `resources/views/chat/` | builder-4 |
| E | Frontend pages theme fixes | `resources/views/tasks/`, `resources/views/projects/`, `resources/views/departments/`, `resources/views/dashboard.blade.php` | builder-5 |

## File Conflict Prevention

- Track A and B: different files — safe
- Track C touches `routes/api.php` — only Track C
- Track D: ONLY `layouts/app.blade.php` and `chat/index.blade.php`
- Track E: all other blade files EXCEPT `layouts/app.blade.php`
- If User.php needs changes from both A and C: Track A does casts fix, Track C does dashboard methods fix (different parts of file)

## Verification Commands

```bash
php artisan route:list --json    # Routes register correctly
php artisan test --no-interaction # Tests pass
```
