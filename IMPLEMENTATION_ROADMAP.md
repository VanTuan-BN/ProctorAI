# S-MONITOR: ACTION PLAN & IMPLEMENTATION ROADMAP
## Kế hoạch sửa lỗi theo ưu tiên & tác động

---

## **PHASE 1: CRITICAL FIXES (Ngay lập tức)**
**Deadline**: Before any production deployment
**Impact**: System stability/data integrity

### **1.1: Fix Camera Resource Leak (main_ui.py)**
```
Status: NOT STARTED
Effort: 30 minutes
Risk: LOW
Files affected: main_ui.py (1 file)
```

**Changes needed:**
- Wrap `process_identity_verification()` với try/except/finally
- Move `stop_verify_preview()` to finally block
- Add timer restart logic in finally

**Code locations:**
- Line 1362-1420: process_identity_verification()
- Line 1125-1140: stop_verify_preview()

**Testing checklist:**
- [ ] Click "Quét danh tính" → Network fail → Camera closes properly
- [ ] Camera can be reused after exception
- [ ] Timer restarts when except occurs

---

### **1.2: Fix MySQL Connection Leak (admin_app.py + proctor_app.py)**
```
Status: NOT STARTED
Effort: 45 minutes (2 files)
Risk: LOW
Files affected: admin_app.py, proctor_app.py
```

**Changes needed:**
- Add `try/finally` to all `get_db()` calls
- Move `db.close()` to finally block
- Alternative: Create context manager wrapper

**Code locations:**
- admin_app.py:
  - Line 915-940: upload_face()
  - Line 968-985: load_proctors()
  - Similar patterns in other methods
  
- proctor_app.py:
  - Line 423-450: load_monitor_overview()
  - Similar patterns elsewhere

**Testing checklist:**
- [ ] Upload face 10 times (some fail intentionally) → Monitor DB connection count
- [ ] No "Too many connections" error
- [ ] Connection pool stays healthy

**SQL to monitor:**
```sql
SHOW PROCESSLIST;
SHOW STATUS LIKE 'Threads_connected';
-- Should stay constant, not grow
```

---

### **1.3: Fix File-DB Atomicity (admin_app.py)**
```
Status: NOT STARTED
Effort: 45 minutes
Risk: MEDIUM (logic change)
Files affected: admin_app.py
```

**Changes needed:**
- Reverse order: INSERT into DB first, COPY file second
- Add uploaded_files tracking list
- Proper rollback on failure
- Remove orphan files on exception

**Code locations:**
- Line 903-940: upload_face()

**Testing checklist:**
- [ ] Intentionally fail INSERT (duplicate key) → File NOT copied
- [ ] Intentionally fail COPY → Rollback, DB clean
- [ ] Check server_database/ directory after failures → No orphan files
- [ ] Multi-file upload: if file 3 fails, 1-2 committed, 3-5 rolled back

**SQL to verify:**
```sql
SELECT COUNT(*) FROM student_face_images WHERE msv = 'test_msv';
-- Should match actual file count in server_database/
```

---

## **PHASE 2: HIGH-IMPACT FIXES (Within 1 week)**
**Impact**: Performance + Reliability

### **2.1: Fix Shared Memory Race Condition (main_ui.py)**
```
Status: NOT STARTED
Effort: 1 hour
Risk: MEDIUM (concurrency pattern)
Files affected: main_ui.py, core_ai.py
```

**Changes needed:**
- Enable lock on shared_frame_ids: `lock=True`
- Wrap frame access with `with lock: ...` context
- Add lock documentation

**Code locations:**
- main_ui.py:
  - Line 118-125: Array initialization
  - Line 1014-1025: _resolve_frame_from_payload()
  
- core_ai.py:
  - Frame write locations

**Testing checklist:**
- [ ] 30min stress test: frame_id mismatch detection rate = 0
- [ ] Identity verification confidence scores stable (no jitter)
- [ ] No false intruder alerts from stale frame data

**Metrics to track:**
```python
# Add to config:
FRAME_CONSISTENCY_ERRORS = 0
# In _resolve_frame_from_payload():
if mismatch:
    FRAME_CONSISTENCY_ERRORS += 1
    # Log/alert if > 0 per session
```

---

### **2.2: Add Image Quality Validation (admin_app.py)**
```
Status: NOT STARTED
Effort: 1.5 hours
Risk: MEDIUM (adds validation logic)
Files affected: admin_app.py
```

**Changes needed:**
- Create `validate_enrollment_image()` function
- Check: file size, resolution, face detection, blur, brightness
- Reject invalid images with clear error messages
- Allow retry with rejection reasons shown

**Code locations:**
- Line 903-940: upload_face() (add validation before upload)

**Validation criteria (from ENROLLMENT_IMAGE_STANDARD.md):**
- ✓ File size: ≤ 10MB
- ✓ Resolution: 640x480 minimum
- ✓ Face detected: Exactly 1 face
- ✓ Face size: ≥ 20% width, ≥ 25% height
- ✓ Blur: Laplacian variance ≥ 50
- ✓ Brightness: 40-220 range

**Testing checklist:**
- [ ] Upload clear, good face photo → Accepted
- [ ] Upload blurry photo → Rejected (blur_score < 50)
- [ ] Upload landscape (face too small) → Rejected
- [ ] Upload 50MB file → Rejected (size too large)
- [ ] Upload group photo (multiple faces) → Rejected

---

### **2.3: Fix Queue Drop Feedback (main_ui.py)**
```
Status: NOT STARTED
Effort: 30 minutes
Risk: LOW (logging + UI change)
Files affected: main_ui.py
```

**Changes needed:**
- Change logger.debug → logger.warning
- Add UI feedback when queue drops occur
- Track drop counter per track
- Mark track suspicious if drops > 3

**Code locations:**
- Line 454-468: _queue_crop()

**Testing checklist:**
- [ ] Queue fill up → WARNING in logs (not debug)
- [ ] UI shows "⚠️ Hệ thống đang chậm" message
- [ ] Monitor feedback text updates in real-time

---

## **PHASE 3: MEDIUM-PRIORITY FIXES (Within 2 weeks)**
**Impact**: Code quality + UX

### **3.1: Fix Duplicate Return (api_server.py)**
```
Status: NOT STARTED
Effort: 15 minutes
Risk: LOW
Files affected: api_server.py
```

**Changes needed:**
- Remove unreachable return statement
- Reorder logic: check fail cases first, success last

**Code locations:**
- Line 1036-1038: student_login()

**Testing checklist:**
- [ ] Both login success and failure paths reachable
- [ ] Run through static analysis (pylint, etc)

---

### **3.2: Fix N+1 Reference Fetch (api_server.py + main_ui.py)**
```
Status: NOT STARTED
Effort: 2 hours
Risk: MEDIUM (API change)
Files affected: api_server.py (backend), main_ui.py (client)
```

**Changes needed:**

**Backend (api_server.py):**
- Modify `/api/get_face_refs/{msv}` endpoint
- Return images inline as base64 instead of just metadata
- Return single response instead of N separate requests

**Frontend (main_ui.py):**
- Change `_load_reference_face()` to use batch endpoint
- Decode base64 images locally

**Code locations:**
- api_server.py: Line 1140-1170 (GET /api/get_face_refs endpoint)
- main_ui.py: Line 982-1008 (_load_reference_face)

**Performance impact:**
```
Before: 50ms (metadata) + 5 * 100ms (per image) = 550ms
After: 1 * 150ms (batch with images) = 150ms
Improvement: 3.7× faster
```

**Testing checklist:**
- [ ] Verify latency reduced from 500ms → 150ms
- [ ] All 5-10 reference images loaded correctly
- [ ] Fallback to single image if batch fails
- [ ] Decrypt base64 accurately

---

## **PHASE 4: NICE-TO-HAVE IMPROVEMENTS (Nice to have)**
**Impact**: Developer experience + monitoring

### **4.1: Add Comprehensive Error Logging**
```
Status: NOT STARTED
Effort: 1 hour
Risk: LOW
Files affected: All 3 files
```

**Changes:**
- Ensure all exceptions logged at appropriate level (INFO/WARNING/ERROR)
- Add context (msv, session_token, etc) to logs
- Create log dashboard (status page)

---

## **IMPLEMENTATION SCHEDULE**

| Phase | Task | Duration | Start | End | Owner |
|-------|------|----------|-------|-----|-------|
| 1 | Camera leak | 0.5h | Day 1 | Day 1 | You |
| 1 | DB connection leak | 0.75h | Day 1 | Day 1 | You |
| 1 | File-DB atomicity | 0.75h | Day 2 | Day 2 | You |
| 2 | Shared memory race | 1h | Day 3 | Day 3 | You |
| 2 | Image validation | 1.5h | Day 4-5 | Day 5 | You |
| 2 | Queue feedback | 0.5h | Day 5 | Day 5 | You |
| 3 | Duplicate return | 0.25h | Day 6 | Day 6 | You |
| 3 | N+1 fetch | 2h | Day 7-8 | Day 8 | You |
| 4 | Error logging | 1h | Day 9 | Day 9 | You |

**Total effort: ~9 hours of coding**

---

## **TESTING STRATEGY**

### **Unit Tests (Per Fix)**
```python
# Example structure
class TestCameraLeakFix:
    def test_camera_closes_on_network_error(self):
        # Start verify
        # Inject network error
        # Assert camera.isOpened() == False
        pass
    
    def test_timer_restarts_on_exception(self):
        # Start verify
        # Inject exception
        # Assert timer.isActive() == True
        pass

class TestImageValidation:
    def test_valid_image_accepted(self):
        # Create valid test image
        # assert validate_enrollment_image() == (True, "OK")
        pass
    
    def test_blurry_image_rejected(self):
        # Create blurry test image
        # assert validate_enrollment_image()[0] == False
        pass
```

### **Integration Tests**
```python
# Smoke test entire upload flow
class TestAdminUploadFlow:
    def test_single_valid_image_upload(self):
        # Upload valid image → Assert in DB + on disk
        pass
    
    def test_mixed_valid_invalid_batch(self):
        # Upload 3 valid + 2 invalid
        # Assert 3 in DB, 2 rejected with reasons
        pass
    
    def test_connection_resilience(self):
        # Simulate DB connection failure during upload
        # Assert cleanup happens, can retry
        pass
```

### **Performance Tests**
```python
# Measure before/after improvements
def test_reference_fetch_latency():
    import time
    start = time.time()
    # Load 5 reference images
    elapsed = time.time() - start
    assert elapsed < 0.2, f"Reference fetch too slow: {elapsed}s"
```

### **Stress Tests**
```python
# Concurrent load testing
def test_db_connection_pool_under_load():
    # 50 concurrent upload_face() calls
    # Monitor connection pool
    # Assert no "Too many connections" error
```

---

## **DEPLOYMENT CHECKLIST**

### **Pre-deployment**
- [ ] All Phase 1 fixes implemented
- [ ] Unit tests pass (100% Phase 1 coverage)
- [ ] Integration tests pass
- [ ] Code review completed
- [ ] Database schema migrated (if needed)

### **Deployment**
- [ ] Backup current DB
- [ ] Deploy code to staging
- [ ] Run smoke tests on staging
- [ ] Deploy to production
- [ ] Monitor logs for errors

### **Post-deployment**
- [ ] Monitor CPU/memory (camera leak check)
- [ ] Monitor DB connection count (no leak)
- [ ] Track upload success rate (atomicity check)
- [ ] Monitor verify latency (no regression)

### **Rollback Plan**
If critical issue found within 24h:
- [ ] Kill processes gracefully
- [ ] Rollback to previous version
- [ ] Restore DB from backup
- [ ] Communicate with users

---

## **METRICS TO TRACK POST-FIX**

### **System Health**
```
1. Camera resource:
   - Crashes with "Camera locked" error: Should be 0
   - Camera availability: Should be 100%

2. Database connection:
   - Active connections: Should stay constant
   - Connection errors: Should be 0
   - Query latency: Should be stable

3. File system:
   - Orphan files: Should be 0
   - Used storage: Should match DB records
   - Cleanup success rate: Should be 100%

4. Verification:
   - Frame drop rate: Should be < 0.1%
   - Verify latency: Should be 100-200ms (not 500ms)
   - False negative rate: Should be < 2%
```

### **User Experience**
```
1. Success rate:
   - Enrollment success on first try: Target 95%
   - Verify success rate: Target 98%

2. Latency:
   - Enrollment process: < 20 seconds
   - Verify process: < 15 seconds

3. Error clarity:
   - Users understand rejection reasons: Survey 80%
   - Support tickets due to confusing errors: Reduce by 50%
```

---

## **KNOWN RISKS & MITIGATIONS**

| Risk | Impact | Mitigation |
|------|--------|-----------|
| Database migration break | Data loss | Backup before, dry-run on staging |
| Context manager behavior unexpected | Logic error | Extensive unit tests |
| Performance regression | User timeout | Load test before deploy |
| Image validation too strict | User frustration | Adjust thresholds based on real data |
| Race condition not fully fixed | Silent failures | Monitor metrics closely first week |

---

## **SUCCESS CRITERIA**

✅ **All Phase 1 fixes deployed**
✅ **System runs 7 days without resource leaks**
✅ **Upload/verify success rate > 95%**
✅ **Verify latency < 200ms (p95)**
✅ **Zero unhandled exceptions in logs**
✅ **Users report better stability**

