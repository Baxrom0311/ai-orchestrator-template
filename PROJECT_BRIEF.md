# TMS (Task Management System) — Autonomous Quality Assurance

## Goal

TMS loyihasini to'liq skanerlash, barcha mavjud buglarni topish va fix qilish. Agent o'zi erkin harakat qiladi — o'zi izlaydi, o'zi testlaydi, o'zi fix qiladi.

**Oldingi sprint da 18 ta bug fix qilindi. Endi qolgan muammolarni topish va tuzatish kerak.**

---

## Tech Stack

- **Backend:** Laravel 12 (PHP 8.2+)
- **Frontend:** Blade templates + CoreUI Bootstrap Admin Template
- **Theme:** CoreUI dark/light mode (`data-coreui-theme` attribute)
- **CSS:** CoreUI style.css + custom inline styles in `layouts/app.blade.php`
- **Auth:** RanchID OAuth2 (JWT), Sanctum, Spatie Permission
- **Database:** MySQL (production), SQLite (local)
- **Notifications:** Firebase FCM, Laravel Notifications, Pusher/Reverb

---

## Project Path

`D:/Projects/TMS`

---

## AUTONOMOUS SCAN INSTRUCTIONS

Agent o'zi quyidagi sohalarda muammolarni izlashi va fix qilishi KERAK:

### 1. Frontend Dark/Light Theme Scan

```bash
# Oq background qolgan joylar
grep -rn "bg-white" resources/views/ --include="*.blade.php"
grep -rn "text-dark" resources/views/ --include="*.blade.php"
grep -rn "text-white" resources/views/ --include="*.blade.php"
grep -rn 'style="background.*white' resources/views/ --include="*.blade.php"
grep -rn 'style="background.*#fff' resources/views/ --include="*.blade.php"
grep -rn 'style="color.*black' resources/views/ --include="*.blade.php"
grep -rn 'style="color.*#000' resources/views/ --include="*.blade.php"
grep -rn "bg-light" resources/views/ --include="*.blade.php"

# Dark mode da ko'rinmas elementlar
grep -rn "border-dark" resources/views/ --include="*.blade.php"
grep -rn 'style="background-color' resources/views/ --include="*.blade.php"
```

**Fix strategy:**
- `bg-white` → `bg-body`
- `text-dark` → `text-body`
- `text-white` (on light bg) → `text-body-emphasis`
- `bg-light` → `bg-body-tertiary`
- Inline `style="background: white"` → olib tashlash
- Dark mode override kerak bo'lsa → `layouts/app.blade.php` `<style>` blokiga qo'shish:
  ```css
  [data-coreui-theme="dark"] .selector { property: value !important; }
  ```

### 2. Backend Logic Scan

```bash
# Hardcoded status values (Enum ishlatilmagan)
grep -rn "->where('status'," app/ --include="*.php" | grep -v "Enum"
grep -rn "where('status', [0-9]" app/ --include="*.php"

# Nullable access without ?->
grep -rn '\$request->' app/Services/ --include="*.php" | grep -v '?->'

# Undefined variables risk
grep -rn "if.*\$request" app/Services/ --include="*.php"

# N+1 query patterns
grep -rn "->each\|->map" app/ --include="*.php" | grep -v "collect"
```

**Fix strategy:**
- Hardcoded integers → `TaskStatusEnum::VALUE->value`
- `$request->field` → `$request?->field` (agar nullable bo'lsa)
- N+1 → eager loading (`with()`, `loadMissing()`)

### 3. Route & Controller Scan

```bash
# Duplicate route names
php artisan route:list --json | python -c "import json,sys,collections; routes=json.loads(sys.stdin.read()); names=[r['name'] for r in routes if r.get('name')]; dupes=[n for n,c in collections.Counter(names).items() if c>1]; print(dupes)"

# Missing middleware
grep -rn "Route::" routes/ --include="*.php" | grep -v "middleware"
```

### 4. View/Blade Errors

```bash
# Undefined variables in views
grep -rn '\$[a-zA-Z]' resources/views/ --include="*.blade.php" | grep -v "@\|{{\|--\|//"

# Broken asset paths
grep -rn "asset(" resources/views/ --include="*.blade.php" | grep -v "assets/"
```

### 5. Security Scan

```bash
# Raw SQL without bindings
grep -rn "DB::raw\|whereRaw\|selectRaw" app/ --include="*.php"

# Missing CSRF
grep -rn '<form' resources/views/ --include="*.blade.php" | grep -v "@csrf"

# Exposed secrets
grep -rn "password\|secret\|token" app/ --include="*.php" | grep -v "->password\|password_hash\|csrf_token\|remember_token"
```

---

## PREVIOUSLY FIXED BUGS (reference — do not re-fix)

These were fixed in the previous sprint. Verify they are still fixed, but don't redo them:

1. BUG-1: User Model duplicate casts → merged into casts() method
2. BUG-2: ProjectService::store() undefined $file_group_id → default null
3. BUG-3: DepartmentService::update() stale comparison → save old boss before update
4. BUG-4: TaskRejectService::store() inconsistent status → recalculateTaskStatus
5. BUG-5: Double notification → removed from TaskService::store()
6. BUG-6: Department::allChildren() N+1 → loadMissing
7. BUG-7: User model hardcoded status → Enum values
8. BUG-8: Inbox routes outside auth group → moved inside
9. BUG-9: TaskService::store() nullable request → ?->
10. BUG-10: ProjectService::update() double update → removed duplicate
11. FE-BUG-1 to FE-BUG-8: Dark mode theme fixes

---

## Acceptance Criteria

1. ✅ `php artisan test --no-interaction` — barcha testlar o'tadi
2. ✅ `php artisan route:list --json` — xatosiz
3. ✅ Dark mode da oq/ko'rinmas joylar yo'q (grep scan toza)
4. ✅ Light mode da qora/ko'rinmas joylar yo'q
5. ✅ Hardcoded status integers yo'q (Enum ishlatilgan)
6. ✅ Nullable access xavfsiz (?-> ishlatilgan)
7. ✅ Yangi buglar topilmagan (discovery scan toza)
8. ✅ Git push qilinMAYDI

---

## Non-goals

- Yangi feature qo'shish
- Database migration o'zgartirish
- Git push / deployment
- CoreUI template ni almashtirish
- Performance optimization (agar bug bo'lmasa)

---

## Constraints

- `app/` va `resources/views/` o'zgartiriladi
- `routes/` o'zgartirilishi mumkin
- `public/assets/css/` ga yangi fayl qo'shish mumkin
- `database/migrations/` ga TEGMASLIK
- `vendor/` ga TEGMASLIK
- Git push qilinMAYDI
- Agent o'zi mustaqil qaror qabul qiladi — qaysi faylni fix qilish kerakligini
