# EduSense — Full-Feature Demo Script

A scenario-based walkthrough that exercises **every implemented feature** in a
realistic user journey. Copy-paste the PowerShell blocks in order; each block
states what to observe.

> **Before starting**: complete the setup in the project README (Docker stack
> up + `/health/ready` returning `ready`, Alembic migrated to head, Celery
> worker AND Beat running, `GEMINI_API_KEY` set for the AI scenarios).

Throughout: `$token` holds the logged-in user's access token; `$headers` the
auth header. AI calls consume the free plan quota (10 requests/day) — see
§8 to raise it mid-demo.

---

## §1 — Authentication & Profiles (M1)

### 1.1 Register
```powershell
$base = "http://localhost:8000/api/v1"
$register = Invoke-RestMethod -Method Post -Uri "$base/auth/register" `
  -ContentType "application/json" `
  -Body '{"full_name":"Demo Student","email":"demo@edusense.vn","password":"Passw0rd123"}'
$register.data
```
**Observe**: `user_id`, `is_email_verified: False`. Try the same call again →
`409` duplicate email. Try a weak password (`"12345678"`) → `422` with the
policy message.

### 1.2 Login + refresh + logout
```powershell
$login = Invoke-RestMethod -Method Post -Uri "$base/auth/login" `
  -ContentType "application/json" `
  -Body '{"email":"demo@edusense.vn","password":"Passw0rd123"}'
$token  = $login.data.access_token
$refresh = $login.data.refresh_token
$headers = @{ Authorization = "Bearer $token" }
$login.data                      # access_token, refresh_token, expires_in=900

Invoke-RestMethod -Method Post -Uri "$base/auth/refresh" `
  -ContentType "application/json" `
  -Body (@{ refresh_token = $refresh } | ConvertTo-Json)     # new access token

Invoke-RestMethod -Method Post -Uri "$base/auth/logout" `
  -Headers $headers -ContentType "application/json" `
  -Body (@{ refresh_token = $refresh } | ConvertTo-Json)     # session revoked
```
**Observe**: wrong password and unknown email return the **same** generic 401
(no account enumeration). Logout twice → still 200 (no session-existence leak).

> Re-login after this block (logout revoked the refresh session) and refresh
> `$token`/`$headers`.

### 1.3 Password reset (demo trick — raw token never leaves the DB in prod)
```powershell
# Request (always 200, identical for unknown emails — no enumeration)
Invoke-RestMethod -Method Post -Uri "$base/auth/password/reset-request" `
  -ContentType "application/json" -Body '{"email":"demo@edusense.vn"}'
```
The raw token only exists in the reset email (SMTP off in the demo). To
simulate delivery, overwrite the stored hash with the hash of a known token:
```powershell
$rawToken = "demo-reset-token-0123456789abcdef"
$hash = -join ([System.Security.Cryptography.SHA256]::Create().ComputeHash(
    [Text.Encoding]::UTF8.GetBytes($rawToken)) | ForEach-Object { $_.ToString("x2") })
docker exec edusense-postgres psql -U edusense_user -d edusense_db -c `
  "UPDATE verification_tokens SET token_hash='$hash' WHERE purpose='password_reset' AND consumed_at IS NULL;"

Invoke-RestMethod -Method Post -Uri "$base/auth/password/reset-confirm" `
  -ContentType "application/json" `
  -Body (@{ token = $rawToken; new_password = "NewPass0rd123" } | ConvertTo-Json)
```
**Observe**: confirm → 200; old password now fails 401; new password logs in.

### 1.4 Profile CRUD
```powershell
Invoke-RestMethod -Uri "$base/users/me" -Headers $headers
Invoke-RestMethod -Method Put -Uri "$base/users/me/profile" -Headers $headers `
  -ContentType "application/json" `
  -Body '{"display_name":"Demo","university":"USTH","academic_year":4,"major":"AI"}'
```
**Observe**: PATCH-style update (only provided fields), `academic_year: 11` →
422 (schema CHECK mirrored).

### 1.5 Login rate limiting (NFR04)
```powershell
for ($i=0; $i -lt 6; $i++) {
  try { Invoke-RestMethod -Method Post -Uri "$base/auth/login" `
    -ContentType "application/json" `
    -Body '{"email":"demo@edusense.vn","password":"WrongPass1"}' } catch { $_.Exception.Response.StatusCode.value__ }
}
# then even with the CORRECT password:
Invoke-RestMethod -Method Post -Uri "$base/auth/login" `
  -ContentType "application/json" -Body '{"email":"demo@edusense.vn","password":"NewPass0rd123"}'
```
**Observe**: first 5 failures → 401; 6th → **429 even with correct password**;
security events recorded in `audit_security_events`. Reset for the demo:
```powershell
docker exec edusense-postgres psql -U edusense_user -d edusense_db -c "DELETE FROM login_attempts;"
```

---

## §2 — Academic Study Core (M2)

### 2.1 Courses
```powershell
$course = Invoke-RestMethod -Method Post -Uri "$base/courses" -Headers $headers `
  -ContentType "application/json" `
  -Body '{"name":"Machine Learning","course_code":"CS402","semester":"HK1","academic_year":"2026-2027","instructor":"Dr. Phong"}'
$courseId = $course.data.id
```
**Observe**: duplicate natural key (same code+semester+year) → `409`.

### 2.2 Assignments (due tomorrow — feeds reminders in §6)
```powershell
$due = (Get-Date).AddDays(1).ToString("yyyy-MM-ddTHH:mm:sszzz")
$asg = Invoke-RestMethod -Method Post -Uri "$base/courses/$courseId/assignments" `
  -Headers $headers -ContentType "application/json" `
  -Body (@{ title = "HW1 - Gradient Descent"; due_at = $due; priority = "high" } | ConvertTo-Json)
$asg.data    # status pending, priority high
```

### 2.3 Exams (in 3 days — feeds calendar + reminders)
```powershell
$examAt = (Get-Date).AddDays(3).ToString("yyyy-MM-ddTHH:mm:sszzz")
Invoke-RestMethod -Method Post -Uri "$base/courses/$courseId/exams" -Headers $headers `
  -ContentType "application/json" `
  -Body (@{ title = "Midterm"; exam_at = $examAt; duration_minutes = 90; location = "Hall B" } | ConvertTo-Json)
```

### 2.4 Weekly schedules + overlap warning
```powershell
$monday = (Get-Date).Date.AddDays( ((8 + [int](Get-Date).DayOfWeek) % 7) ).ToString("yyyy-MM-dd")
$friday = (Get-Date).Date.AddDays( ((8 + [int](Get-Date).DayOfWeek) % 7) + 90 ).ToString("yyyy-MM-dd")
$slot = @{ day_of_week = 1; start_time = "08:00"; end_time = "09:30"; start_date = $monday; end_date = $friday; location = "Room A" }
Invoke-RestMethod -Method Post -Uri "$base/courses/$courseId/schedules" -Headers $headers `
  -ContentType "application/json" -Body ($slot | ConvertTo-Json)                      # no warnings
Invoke-RestMethod -Method Post -Uri "$base/courses/$courseId/schedules" -Headers $headers `
  -ContentType "application/json" -Body (($slot | ConvertTo-Json -Compress).Replace('"08:00"','"08:30"') | ConvertFrom-Json | ConvertTo-Json)
```
**Observe**: the second slot (08:30–10:00) overlaps the first → response has
`warnings: [...]` but is still created (spec: warn, not block). Invalid input
(`end_time` before `start_time`, `day_of_week: 7`) → 422.

### 2.5 Aggregated calendar
```powershell
$mon = (Get-Date).Date.ToString("yyyy-MM-dd"); $sun = (Get-Date).Date.AddDays(6).ToString("yyyy-MM-dd")
Invoke-RestMethod -Uri "$base/calendar?start=$mon&end=$sun" -Headers $headers
```
**Observe**: one merged, chronologically sorted list; items typed `class` /
`exam` / `assignment_deadline` (client colors/icons them differently).

---

## §3 — Documents & AI Ingestion (M3)

### 3.1 Upload (magic-byte validation)
```powershell
# Any real PDF works; a text file renamed .pdf is REJECTED:
curl.exe -X POST "$base/documents" -H "Authorization: Bearer $token" `
  -F "files=@C:\path\to\lecture.pdf;type=application/pdf" -F "course_id=$courseId" -F "title=ML Lecture 1"
curl.exe -X POST "$base/documents" -H "Authorization: Bearer $token" `
  -F "files=@C:\path\to\fake.pdf;type=application/pdf"   # a text file renamed .pdf
```
**Observe**: valid upload → 201 `status: "pending"`; the renamed text file →
422 "content does not match its declared type". With the worker running, the
document flips `pending → processing → embedded` within seconds — watch the
worker terminal (extract → chunk → embed) and re-list documents to confirm.

### 3.2 Search, tags, download
```powershell
$docId = (Invoke-RestMethod -Uri "$base/documents?q=lecture" -Headers $headers).data[0].id
Invoke-RestMethod -Method Post -Uri "$base/documents/$docId/tags" -Headers $headers `
  -ContentType "application/json" -Body '{"name":"ml"}'
Invoke-RestMethod -Uri "$base/documents?tag=ml" -Headers $headers
Invoke-RestMethod -Uri "$base/documents/$docId/download" -Headers $headers   # presigned URL → open in browser
```

### 3.3 Summarization job (worker)
```powershell
$sumJob = Invoke-RestMethod -Method Post -Uri "$base/ai/documents/$docId/summary" -Headers $headers
Start-Sleep -Seconds 15
Invoke-RestMethod -Uri "$base/ai/jobs/$($sumJob.data.id)" -Headers $headers
```
**Observe**: 202 → poll → `status: succeeded`, `result.summary` in Vietnamese,
`ai_generated: true`. Re-request → the SAME job (idempotent).

### 3.4 Question bank generation (MCQ + essay)
```powershell
$qJob = Invoke-RestMethod -Method Post -Uri "$base/ai/documents/$docId/questions" `
  -Headers $headers -ContentType "application/json" `
  -Body '{"count":5,"question_type":"multiple_choice"}'
Start-Sleep -Seconds 20
Invoke-RestMethod -Uri "$base/ai/question-banks" -Headers $headers
$bankId = (Invoke-RestMethod -Uri "$base/ai/question-banks" -Headers $headers).data[0].id
Invoke-RestMethod -Uri "$base/ai/question-banks/$bankId/questions" -Headers $headers
```
**Observe**: MCQs come with `options` arrays, `correct_answer`, `explanation`.

---

## §4 — Chat & Practice (M4)

### 4.1 AI chat (quota-gated, usage-logged)
```powershell
$conv = Invoke-RestMethod -Method Post -Uri "$base/chat/conversations" -Headers $headers `
  -ContentType "application/json" -Body '{"title":"Study help"}'
Invoke-RestMethod -Method Post `
  -Uri "$base/chat/conversations/$($conv.data.conversation.id)/messages" `
  -Headers $headers -ContentType "application/json" `
  -Body '{"content":"Explain gradient descent in one paragraph."}'
```
**Observe**: response labeled `ai_generated: true`, per-message token counts;
quota state (`remaining`) decreases per AI call.

### 4.2 Practice attempts (MCQ auto-grade + essay rubric grading)
```powershell
$attempt = Invoke-RestMethod -Method Post -Uri "$base/practice/attempts" `
  -Headers $headers -ContentType "application/json" `
  -Body (@{ question_bank_id = $bankId } | ConvertTo-Json)
$attemptId = $attempt.data.id
$questions = (Invoke-RestMethod -Uri "$base/ai/question-banks/$bankId/questions" -Headers $headers).data

# Answer everything (MCQ: pick an option letter; essay: free text)
$answers = $questions | ForEach-Object {
  if ($_.question_type -eq "multiple_choice") {
    @{ question_id = $_.id; selected_option = ($_.options[0]) }
  } else {
    @{ question_id = $_.id; answer_text = "Gradient descent điều chỉnh tham số theo hướng giảm gradient của hàm mất mát, với learning rate xác định bước đi." }
  }
}
Invoke-RestMethod -Method Post -Uri "$base/practice/attempts/$attemptId/submit" `
  -Headers $headers -ContentType "application/json" -Body (@{ answers = $answers } | ConvertTo-Json -Depth 5)

Invoke-RestMethod -Uri "$base/practice/attempts/$attemptId" -Headers $headers
```
**Observe**: MCQ correct → full per-question score, wrong → 0 with the right
answer in feedback; essays get a model score + Vietnamese feedback +
rubric breakdown; attempt `total_score` = sum, status `graded`. Submitting
again → `409`.

---

## §5 — Study Plans (M5)

```powershell
$planStart = (Get-Date).Date.ToString("yyyy-MM-dd")
$planEnd   = (Get-Date).Date.AddDays(13).ToString("yyyy-MM-dd")
$planJob = Invoke-RestMethod -Method Post -Uri "$base/study-plans" -Headers $headers `
  -ContentType "application/json" `
  -Body (@{ name = "2-week revision"; start_date = $planStart; end_date = $planEnd } | ConvertTo-Json)
Start-Sleep -Seconds 15

$plans = Invoke-RestMethod -Uri "$base/study-plans" -Headers $headers
$planId = $plans.data[0].id
Invoke-RestMethod -Uri "$base/study-plans/$planId" -Headers $headers     # plan + items

$itemId = (Invoke-RestMethod -Uri "$base/study-plans/$planId" -Headers $headers).data.items[0].id
Invoke-RestMethod -Method Patch -Uri "$base/study-plans/items/$itemId/status" `
  -Headers $headers -ContentType "application/json" -Body '{"status":"accepted"}'
```
**Observe**: AI-generated items land inside the requested window (out-of-window
items are dropped); acceptance stamps `accepted_at`; completion stamps
`completed_at`.

---

## §6 — Reminders & Notifications (M5)

Beat scans hourly/6-hourly — for the demo, trigger the scan immediately
(the assignment from §2.2 is due within 24 h, so it qualifies):

```powershell
cd D:\zcodee\test2\EduSence\backend
.venv\Scripts\celery.exe -A app.worker.celery_app.celery_app call app.worker.tasks.emails.deadline_reminder_scan
```
```powershell
Invoke-RestMethod -Uri "$base/notifications" -Headers $headers
Invoke-RestMethod -Uri "$base/notifications?unread_only=true" -Headers $headers
Invoke-RestMethod -Method Post -Uri "$base/notifications/<notif_id>/read" -Headers $headers
```
**Observe**: one in-app reminder with dedup key — re-running the scan the same
day creates **no duplicates**; with SMTP configured, an email also goes out and
the notification flips to `sent`.

---

## §7 — Dashboard (M5)

```powershell
Invoke-RestMethod -Uri "$base/dashboard/overview" -Headers $headers
Invoke-RestMethod -Uri "$base/dashboard/progress" -Headers $headers
```
**Observe**: deadlines/exams within 7 days, overdue count; per-course weighted
progress (assignments + committed study items) with risk status
(`on_track` / `at_risk` / `overdue`).

---

## §8 — Admin & RBAC (M6)

Promote the demo user (one-time):
```powershell
docker exec edusense-postgres psql -U edusense_user -d edusense_db -c `
  "INSERT INTO user_roles (user_id, role_id) SELECT u.id, r.id FROM users u, roles r WHERE u.email='demo@edusense.vn' AND r.name='admin' ON CONFLICT DO NOTHING;"
```
```powershell
Invoke-RestMethod -Uri "$base/admin/users" -Headers $headers                     # list/search accounts

# Suspend a second user, then verify suspended login is blocked:
Invoke-RestMethod -Method Patch -Uri "$base/admin/users/<other_user_id>" -Headers $headers `
  -ContentType "application/json" -Body '{"is_active":false}'
# that user's next login → 403 suspended (and all their sessions were revoked)

Invoke-RestMethod -Uri "$base/admin/ai/usage" -Headers $headers                  # per-user/day token totals
Invoke-RestMethod -Uri "$base/admin/ai/usage/features" -Headers $headers         # per-feature breakdown
Invoke-RestMethod -Method Put -Uri "$base/admin/ai/quota-policies/free" -Headers $headers `
  -ContentType "application/json" `
  -Body '{"request_limit_per_day":50,"token_limit_per_day":null,"warning_threshold_percent":80}'
Invoke-RestMethod -Uri "$base/admin/system/storage" -Headers $headers            # storage totals + top consumers
Invoke-RestMethod -Uri "$base/admin/system/resources" -Headers $headers          # users/courses/jobs counts
```
**Observe**: a STUDENT token on any `/admin/*` path → **403 Forbidden** with no
details (RBAC). Raising the free-plan limit takes effect on the next AI call.

### Quota exhaustion demo (optional)
Temporarily set `request_limit_per_day: 2`, make 3 chat calls → the third
returns `429 Daily AI request quota exceeded.` — fail-closed, counter rolled
back. Restore afterwards.

---

## §9 — Security spot-checks (no token games)

```powershell
Invoke-RestMethod -Uri "$base/courses"                                  # 401 (no token)
Invoke-RestMethod -Uri "$base/courses" -Headers @{Authorization="Bearer refresh-token-here"}  # 401 (wrong type)
Invoke-RestMethod -Uri "$base/courses/<someone-elses-id>" -Headers $headers                   # 404 (never 403 — no existence oracle)
```

---

## Reset between demo runs

```powershell
docker exec edusense-postgres psql -U edusense_user -d edusense_db -c "DELETE FROM login_attempts;"   # clear lockouts
# Full reset of business data (keeps schema):
docker exec edusense-postgres psql -U edusense_user -d edusense_db -c "TRUNCATE notifications, audit_security_events, practice_answer_results, practice_attempts, question_banks, study_plan_items, study_plans, answer_citations, ai_messages, ai_conversations, ai_usage_logs, document_tags, tags, document_chunks, documents, class_schedules, exams, assignments, courses, refresh_sessions, verification_tokens, user_roles, user_profiles, users CASCADE;"
```
(`TRUNCATE ... CASCADE` wipes demo data but keeps plans/roles/policies — do
NOT drop tables. Re-promote the admin role afterwards since users are gone.)

## Known demo limitations

- **Google social login** needs a real Google ID token (Google console setup) — skipped in the live demo; covered by unit tests.
- **Password-reset email delivery** needs SMTP; §1.3 shows the DB-level workaround.
- **Live payments (M6-01..03)** are blocked by DECISION-PO-02 — not demoable until pricing is approved.
