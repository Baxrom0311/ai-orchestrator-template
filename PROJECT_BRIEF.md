# TMS (Task Management System) — Full Bug Fix Sprint

## Goal

TMS loyihasidagi barcha aniqlangan backend VA frontend buglarni fix qilish. Dark/Light theme muammolari, rang tushmasliklari, va backend logic buglarni parallel ravishda tuzatish.

---

## Tech Stack

- **Backend:** Laravel 12 (PHP 8.2+)
- **Frontend:** Blade templates + CoreUI Bootstrap Admin Template
- **Theme:** CoreUI dark/light mode (`data-coreui-theme` attribute)
- **CSS:** CoreUI style.css + custom inline styles in app.blade.php
- **Auth:** RanchID OAuth2 (JWT), Sanctum, Spatie Permission
- **Database:** MySQL (production), SQLite (local)
- **Notifications:** Firebase FCM, Laravel Notifications, Pusher/Reverb

---

## Project Path

`D:/Projects/TMS`

---

## PART 1: BACKEND BUGS (10 ta)

### BUG-1: User Model — Duplicate Casts (CRITICAL)
**File:** `app/Models/User.php`
**Problem:** `$casts` property VA `casts()` method ikkalasi ham mavjud. `$casts` dagi `last_seen => datetime` e'tiborga olinmaydi.
**Fix:** `$casts` property ni olib tashlab, `casts()` method ichiga `'last_seen' => 'datetime'` qo'shish.

### BUG-2: ProjectService::store() — Undefined Variable (CRITICAL)
**File:** `app/Services/ProjectService.php` (store method)
**Problem:** `$file_group_id` undefined bo'lishi mumkin agar ikkala shart ham false bo'lsa.
**Fix:** `$file_group_id = null;` default qiymat berish.

### BUG-3: DepartmentService::update() — Stale Comparison (HIGH)
**File:** `app/Services/DepartmentService.php` (update method)
**Problem:** `$department->update()` dan KEYIN eski boss bilan comparison — doim false.
**Fix:** `$oldBoss = $department->department_boss;` ni update dan OLDIN saqlash.

### BUG-4: TaskRejectService::store() — Inconsistent Status (HIGH)
**File:** `app/Services/TaskRejectService.php`
**Problem:** PENDING da reject → IN_PROGRESS (mantiqsiz). Boshqalarda → REJECTED.
**Fix:** Har doim `recalculateTaskStatus()` chaqirish yoki PENDING da ham REJECTED ga o'tkazish.

### BUG-5: Double Notification (HIGH)
**Files:** `app/Observers/TaskWorkerObserver.php`, `app/Services/TaskNotificationService.php`
**Problem:** Task yaratilganda ikki marta notification: Observer + TaskService.
**Fix:** `TaskService::store()` dan `notifyTaskCreated()` ni olib tashlash (Observer boshqaradi).

### BUG-6: Department::allChildren() — N+1 Query (MEDIUM)
**File:** `app/Models/Department.php`
**Problem:** Recursive method har child uchun alohida query.
**Fix:** `$this->loadMissing('children')` qo'shish.

### BUG-7: User Model — Hardcoded Status Values (MEDIUM)
**File:** `app/Models/User.php`
**Problem:** `->where('status', 2)` o'rniga Enum ishlatilmagan.
**Fix:** `TaskStatusEnum::IN_PROGRESS->value` ishlatish.

### BUG-8: Inbox Routes Outside Auth Group (MEDIUM)
**File:** `routes/api.php`
**Problem:** Inbox routes `check.external.token` group tashqarisida.
**Fix:** Inbox routes ni group ichiga ko'chirish.

### BUG-9: TaskService::store() — Nullable Request (MEDIUM)
**File:** `app/Services/TaskService.php`
**Problem:** `$request->file_group_id` — lekin `$request` nullable.
**Fix:** `$request?->file_group_id` ishlatish.

### BUG-10: ProjectService::update() — Double Update (LOW)
**File:** `app/Services/ProjectService.php`
**Problem:** `file_group_id` ikki marta tekshiriladi va update qilinadi.
**Fix:** Duplicate kodni olib tashlash.

---

## PART 2: FRONTEND / THEME BUGS

### FE-BUG-1: Dark Mode — Card Bodies White Background (CRITICAL)
**Files:** Barcha blade fayllar (`resources/views/**/*.blade.php`)
**Problem:** Ko'p joylarda `.card`, `.card-body`, `.modal-content`, `.form-control`, `.form-select` elementlari dark mode da oq fonda qoladi. CoreUI `data-coreui-theme="dark"` ni to'liq qo'llab-quvvatlamaydi custom komponentlarda.
**Fix:** `resources/views/layouts/app.blade.php` dagi `<style>` blokiga dark mode override lar qo'shish:
```css
[data-coreui-theme="dark"] .card { background-color: var(--cui-body-bg) !important; }
[data-coreui-theme="dark"] .modal-content { background-color: var(--cui-body-bg) !important; color: var(--cui-body-color) !important; }
[data-coreui-theme="dark"] .form-control, [data-coreui-theme="dark"] .form-select { background-color: var(--cui-input-bg) !important; color: var(--cui-body-color) !important; border-color: var(--cui-input-border-color) !important; }
[data-coreui-theme="dark"] .dropdown-menu { background-color: var(--cui-dropdown-bg) !important; }
[data-coreui-theme="dark"] .list-group-item { background-color: var(--cui-body-bg) !important; color: var(--cui-body-color) !important; }
```

### FE-BUG-2: Light Mode — Dark Text on Dark Backgrounds (HIGH)
**Files:** Blade fayllar
**Problem:** Ayrim komponentlarda `text-dark` yoki `bg-dark` class lar hardcoded qo'yilgan. Light mode da yaxshi ko'rinadi, lekin dark mode da ko'rinmaydi (dark text on dark bg).
**Fix:** `text-dark` → `text-body`, `bg-dark` → `bg-body-secondary` yoki CoreUI theme-aware class larga almashtirish. Barcha blade fayllarni grep qilib `text-dark`, `bg-dark`, `bg-white`, `text-white` hardcoded class larni topish va theme-aware alternativlarga almashtirish.

### FE-BUG-3: Sidebar Active Link Color (MEDIUM)
**File:** `resources/views/layouts/sidebar.blade.php`
**Problem:** Active nav-link dark sidebar da ko'rinmasligi mumkin (rang kontrasti past).
**Fix:** `.sidebar .nav-link.active` uchun aniq rang berish.

### FE-BUG-4: Form Inputs in Modals — Dark Mode (MEDIUM)
**Files:** `resources/views/tasks/create.blade.php`, `resources/views/projects/create.blade.php`, va boshqa form blade lar
**Problem:** Modal ichidagi input lar dark mode da oq fonda qoladi.
**Fix:** CoreUI CSS variable lardan foydalanish, inline `style="background: white"` larni olib tashlash.

### FE-BUG-5: Pagination — Dark Mode (MEDIUM)
**Problem:** Laravel pagination links dark mode da oq fonda ko'rinadi.
**Fix:** `[data-coreui-theme="dark"] .pagination .page-link` uchun override qo'shish.

### FE-BUG-6: Badge/Alert Colors in Dark Mode (LOW)
**Problem:** `.badge`, `.alert` elementlari dark mode da kontrast yo'qotadi.
**Fix:** Dark mode uchun badge/alert override lar.

### FE-BUG-7: Chat Page — Dark Mode Issues (HIGH)
**File:** `resources/views/chat/index.blade.php` (104KB — katta fayl)
**Problem:** Chat sahifasi juda katta va ko'p inline style lar bor. Dark mode da message bubble lar, input lar, sidebar oq qoladi.
**Fix:** Inline `background: white`, `color: black` larni olib tashlab, CoreUI class larga almashtirish.

### FE-BUG-8: Dashboard Charts — Dark Mode Text (LOW)
**File:** `resources/views/dashboard.blade.php`
**Problem:** Chart.js label lar dark mode da qora rangda — ko'rinmaydi.
**Fix:** Chart config da `color` ni theme ga moslashtirish (CSS variable yoki JS orqali).

---

## PART 3: ADDITIONAL BUGS TO DISCOVER

Builder agentlar loyihani to'liq skanerlashi kerak:
- `grep` bilan `style="background: white"`, `style="color: black"`, `bg-white`, `text-dark` topish
- Blade fayllarni dark/light mode uchun tekshirish
- PHP xatolar (undefined variable, type mismatch) izlash
- Route conflicts tekshirish

---

## Acceptance Criteria

1. ✅ Barcha 10 ta backend bug fix qilingan
2. ✅ Barcha 8 ta frontend/theme bug fix qilingan
3. ✅ Dark mode da oq joylar yo'q
4. ✅ Light mode da qora/ko'rinmas joylar yo'q
5. ✅ `php artisan route:list` xatosiz ishlaydi
6. ✅ `php artisan test --no-interaction` o'tadi
7. ✅ Hech qanday yangi feature qo'shilmagan
8. ✅ Git push qilinMAYDI

---

## Non-goals

- Yangi feature qo'shish
- JavaScript logic o'zgartirish (faqat theme-related)
- Database migration o'zgartirish
- Git push / deployment
- CoreUI template ni boshqasiga almashtirish

---

## Constraints

- `app/` va `resources/views/` papkalar o'zgartiriladi
- `routes/api.php` o'zgartirilishi mumkin (BUG-8)
- `public/assets/css/` ga YANGI fayl qo'shish mumkin (dark-mode-fixes.css)
- `database/migrations/` ga TEGMASLIK
- `vendor/` ga TEGMASLIK
- Git push qilinMAYDI (`auto_push = false`)
- Parallel 5 agent ishlaydi
