"""
LeadRescuePro Dashboard Backend - Main Application
FastAPI server with all API routes
"""
import os
import json
import uuid
import logging
import csv
import io
import subprocess
from datetime import datetime, date, timedelta, timezone
from pathlib import Path

from fastapi import FastAPI, Depends, HTTPException, UploadFile, File, Form, Query, Body, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, JSONResponse
from sqlalchemy.orm import Session
from sqlalchemy import func, extract

from database import init_db, get_db, User, Prospect, CallLog, Commission, Recording, Client
from auth import verify_pin, hash_pin, create_access_token, get_current_user, require_admin

# ── Setup ────────────────────────────────────────────────────────────────
BASE_DIR = Path(__file__).parent
DATA_DIR = BASE_DIR / "data"
UPLOAD_DIR = DATA_DIR / "uploads"
STATIC_DIR = BASE_DIR / "static"
TEMPLATES_DIR = BASE_DIR / "templates"
PROJECT_DIR = BASE_DIR.parent
os.makedirs(UPLOAD_DIR, exist_ok=True)
os.makedirs(STATIC_DIR, exist_ok=True)
os.makedirs(TEMPLATES_DIR, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler(), logging.FileHandler(str(DATA_DIR / "dashboard.log"))]
)
logger = logging.getLogger("lrp_dashboard")

app = FastAPI(title="LeadRescuePro Dashboard", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "https://fjvnjf.github.io",
        "https://fjvnjf.github.io/lrp-dashboard-frontend",
        "https://lrp-dash.loca.lt",
        "http://localhost:8650",
        "http://127.0.0.1:8650",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*", "abypass-tunnel-reminder", "authorization", "content-type"],
)

@app.middleware("http")
async def localtunnel_bypass_header(request: Request, call_next):
    response = await call_next(request)
    response.headers["abypass-tunnel-reminder"] = "1"
    response.headers["x-lrp-api"] = "leadrescuepro-dashboard"
    return response

if (BASE_DIR / "assets").exists():
    app.mount("/assets", StaticFiles(directory=str(BASE_DIR / "assets")), name="assets")

# ── Whisper Service (lazy loaded) ──────────────────────────────────────
_whisper_model = None

def get_whisper():
    global _whisper_model
    if _whisper_model is None:
        try:
            from faster_whisper import WhisperModel
            logger.info("Loading Whisper tiny model...")
            _whisper_model = WhisperModel("tiny", device="cpu", compute_type="int8")
            logger.info("Whisper model loaded")
        except Exception as e:
            logger.error(f"Failed to load Whisper: {e}")
            return None
    return _whisper_model

# ── Helper functions ─────────────────────────────────────────────────────
DISPOSITION_LABELS = {
    "voicemail": "Voicemail",
    "no_answer": "No Answer",
    "talked_interested": "Talked - Interested",
    "talked_not_interested": "Talked - Not Interested",
    "callback_set": "Callback Set",
    "dnc": "DNC / Not Interested",
    "wrong_number": "Wrong Number",
}

CONNECT_DISPOSITIONS = {"voicemail", "talked_interested", "talked_not_interested", "callback_set"}

def serialize(obj):
    """Convert SQLAlchemy object to dict"""
    if hasattr(obj, '__table__'):
        cols = {c.name: getattr(obj, c.name) for c in obj.__table__.columns}
        for k, v in cols.items():
            if isinstance(v, (datetime, date)):
                cols[k] = v.isoformat()
        return cols
    return obj

def serialize_user(user: User):
    data = serialize(user)
    data.pop("pin_hash", None)
    return data

def utc_day_bounds(days_ago: int = 0):
    """Return naive UTC start/end datetimes for SQLite comparisons."""
    target = datetime.now(timezone.utc).date() - timedelta(days=days_ago)
    start = datetime(target.year, target.month, target.day)
    return start, start + timedelta(days=1)

def score_for_lead(rating, reviews):
    try:
        rating_val = float(rating or 0)
        reviews_val = int(float(reviews or 0))
    except (TypeError, ValueError):
        return "B"
    if rating_val >= 4.5 and reviews_val >= 50:
        return "A"
    if rating_val < 4.0 or reviews_val < 10:
        return "C"
    return "B"

# ── Startup ──────────────────────────────────────────────────────────────
@app.on_event("startup")
def startup():
    init_db()
    logger.info("Database initialized")

# ── Auth Routes ──────────────────────────────────────────────────────────
@app.post("/api/auth/login")
async def login(request: Request, username: str = Form(None), pin: str = Form(None), db: Session = Depends(get_db)):
    # Accept both form-urlencoded and JSON body
    if username is None and pin is None:
        try:
            body = await request.json()
            username = body.get("username")
            pin = body.get("pin")
        except:
            pass
    user = db.query(User).filter(User.username == username).first()
    if not user or not verify_pin(pin, user.pin_hash):
        raise HTTPException(status_code=401, detail="Invalid username or PIN")
    if not user.active:
        raise HTTPException(status_code=403, detail="Account inactive")
    token = create_access_token({"user_id": user.id, "role": user.role, "sub": user.username})
    return {"access_token": token, "token_type": "bearer", "user": serialize_user(user)}

@app.get("/api/auth/me")
def get_me(user: User = Depends(get_current_user)):
    return serialize_user(user)

# ── Prospect Routes ──────────────────────────────────────────────────────
@app.get("/api/prospects")
def list_prospects(
    search: str = Query(None),
    status: str = Query(None),
    score: str = Query(None),
    assigned: int = Query(None),
    limit: int = Query(200),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    query = db.query(Prospect)
    if user.role != "admin":
        query = query.filter(Prospect.assigned_to == user.id)
    if search:
        query = query.filter(Prospect.business_name.ilike(f"%{search}%"))
    if status:
        query = query.filter(Prospect.status == status)
    if score:
        query = query.filter(Prospect.score == score)
    if assigned:
        query = query.filter(Prospect.assigned_to == assigned)
    query = query.order_by(Prospect.created_at.desc()).limit(limit)
    results = [serialize(p) for p in query.all()]
    return {"prospects": results, "total": len(results)}

@app.post("/api/prospects")
def create_prospect(data: dict, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    prospect = Prospect(**{k: v for k, v in data.items() if hasattr(Prospect, k)})
    db.add(prospect)
    db.commit()
    db.refresh(prospect)
    return serialize(prospect)

@app.patch("/api/prospects/{prospect_id}")
def update_prospect(prospect_id: int, data: dict, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    prospect = db.query(Prospect).filter(Prospect.id == prospect_id).first()
    if not prospect:
        raise HTTPException(status_code=404, detail="Prospect not found")
    for k, v in data.items():
        if hasattr(Prospect, k) and k != "id":
            setattr(prospect, k, v)
    db.commit()
    db.refresh(prospect)
    return serialize(prospect)

@app.delete("/api/prospects/{prospect_id}")
def delete_prospect(prospect_id: int, db: Session = Depends(get_db), user: User = Depends(require_admin)):
    prospect = db.query(Prospect).filter(Prospect.id == prospect_id).first()
    if not prospect:
        raise HTTPException(status_code=404, detail="Prospect not found")
    db.delete(prospect)
    db.commit()
    return {"ok": True}

@app.post("/api/prospects/import")
def import_prospects(data: dict, db: Session = Depends(get_db), user: User = Depends(require_admin)):
    """Import prospects from JSON array or CSV text"""
    import csv
    import io
    count = 0
    if isinstance(data.get("leads"), list):
        for item in data["leads"]:
            p = Prospect(
                business_name=item.get("business_name", item.get("name", "Unknown")),
                phone=item.get("phone", ""),
                city=item.get("city", ""),
                state=item.get("state", "TX"),
                rating=item.get("rating"),
                reviews=item.get("reviews"),
                website=item.get("website", ""),
                score=item.get("score", "B"),
            )
            db.add(p)
            count += 1
    elif data.get("csv"):
        reader = csv.DictReader(io.StringIO(data["csv"]))
        for row in reader:
            p = Prospect(
                business_name=row.get("business_name", row.get("name", "Unknown")),
                phone=row.get("phone", ""),
                city=row.get("city", ""),
                state=row.get("state", "TX"),
                rating=float(row["rating"]) if row.get("rating") else None,
                reviews=int(row["reviews"]) if row.get("reviews") else None,
                website=row.get("website", ""),
                score=row.get("score", "B"),
            )
            db.add(p)
            count += 1
    db.commit()
    return {"imported": count}

@app.post("/api/prospects/import-csv")
async def import_prospects_csv(request: Request, db: Session = Depends(get_db), user: User = Depends(require_admin)):
    """Import prospects from raw CSV text body"""
    import csv
    import io
    try:
        body = await request.body()
        csv_text = body.decode("utf-8")
    except:
        raise HTTPException(status_code=400, detail="Invalid CSV body")
    reader = csv.DictReader(io.StringIO(csv_text))
    count = 0
    for row in reader:
        p = Prospect(
            business_name=row.get("business_name", row.get("name", "Unknown")),
            phone=row.get("phone", ""),
            city=row.get("city", ""),
            state=row.get("state", "TX"),
            score=row.get("score", "B"),
        )
        db.add(p)
        count += 1
    db.commit()
    return {"imported": count}

@app.post("/api/prospects/{prospect_id}/assign")
def assign_prospect(prospect_id: int, data: dict, db: Session = Depends(get_db), user: User = Depends(require_admin)):
    prospect = db.query(Prospect).filter(Prospect.id == prospect_id).first()
    if not prospect:
        raise HTTPException(status_code=404, detail="Prospect not found")
    caller_id = data.get("caller_id")
    if not caller_id:
        raise HTTPException(status_code=400, detail="caller_id required")
    caller = db.query(User).filter(User.id == caller_id).first()
    if not caller:
        raise HTTPException(status_code=404, detail="Caller not found")
    prospect.assigned_to = caller_id
    db.commit()
    db.refresh(prospect)
    return serialize(prospect)

# ── Call Log Routes ──────────────────────────────────────────────────────
@app.post("/api/calls/log")
def log_call(data: dict, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    # Auto-find or create prospect
    prospect_id = data.get("prospect_id")
    if not prospect_id:
        business = data.get("business_name", "").strip()
        phone = data.get("phone", "").strip()
        if business:
            existing = db.query(Prospect).filter(
                Prospect.business_name.ilike(f"%{business}%")
            ).first()
            if existing:
                prospect_id = existing.id
            else:
                p = Prospect(business_name=business, phone=phone, status="pending")
                db.add(p)
                db.flush()
                prospect_id = p.id
    
    # Parse call_time
    call_time = datetime.utcnow()
    if data.get("call_time"):
        try:
            call_time = datetime.fromisoformat(data["call_time"].replace("Z", "+00:00"))
        except:
            pass
    
    log = CallLog(
        prospect_id=prospect_id,
        caller_id=user.id,
        business_name=data.get("business_name", ""),
        phone=data.get("phone", ""),
        disposition=data.get("disposition", "voicemail"),
        notes=data.get("notes", ""),
        duration_seconds=data.get("duration_seconds", 0),
        call_time=call_time,
    )
    db.add(log)
    
    # Update prospect status
    if prospect_id:
        prospect = db.query(Prospect).filter(Prospect.id == prospect_id).first()
        if prospect:
            disp = data.get("disposition", "")
            if disp == "talked_interested":
                prospect.status = "contacted"
            elif disp == "callback_set":
                prospect.status = "callback_set"
            elif disp == "dnc" or disp == "wrong_number":
                prospect.status = "dnc"
            else:
                prospect.status = "called"
            prospect.last_contact = call_time
            if not prospect.assigned_to and user.role == "caller":
                prospect.assigned_to = user.id
    
    db.commit()
    db.refresh(log)
    return serialize(log)

@app.get("/api/calls")
def list_calls(
    caller_id: int = Query(None),
    date_filter: str = Query(None, alias="date"),
    disposition: str = Query(None),
    limit: int = Query(100),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    query = db.query(CallLog)
    if user.role != "admin":
        query = query.filter(CallLog.caller_id == user.id)
    if caller_id and user.role == "admin":
        query = query.filter(CallLog.caller_id == caller_id)
    if date_filter == "today":
        today_start, tomorrow_start = utc_day_bounds()
        query = query.filter(CallLog.call_time >= today_start, CallLog.call_time < tomorrow_start)
    elif date_filter:
        try:
            d = datetime.fromisoformat(date_filter).date()
            query = query.filter(func.date(CallLog.call_time) == d)
        except:
            pass
    if disposition:
        query = query.filter(CallLog.disposition == disposition)
    query = query.order_by(CallLog.call_time.desc()).limit(limit)
    return {"calls": [serialize(c) for c in query.all()]}

@app.get("/api/calls/today")
def today_calls(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    today_start, tomorrow_start = utc_day_bounds()
    query = db.query(CallLog).filter(
        CallLog.caller_id == user.id,
        CallLog.call_time >= today_start,
        CallLog.call_time < tomorrow_start
    ).order_by(CallLog.call_time.desc()).limit(200)
    return {"calls": [serialize(c) for c in query.all()]}

@app.get("/api/calls/export")
def export_calls(
    date: str = Query("today"),
    caller_id: int = Query(None),
    db: Session = Depends(get_db),
    user: User = Depends(require_admin),
):
    query = db.query(CallLog)
    if date == "today":
        today_start, tomorrow_start = utc_day_bounds()
        query = query.filter(CallLog.call_time >= today_start, CallLog.call_time < tomorrow_start)
    elif date:
        try:
            d = datetime.fromisoformat(date).date()
            d_start = datetime(d.year, d.month, d.day)
            d_end = d_start + timedelta(days=1)
            query = query.filter(CallLog.call_time >= d_start, CallLog.call_time < d_end)
        except:
            pass
    if caller_id:
        query = query.filter(CallLog.caller_id == caller_id)
    query = query.order_by(CallLog.call_time.desc())
    return {"calls": [serialize(c) for c in query.all()]}

@app.post("/api/calls/{call_id}/recording")
async def upload_recording(call_id: int, file: UploadFile = File(...), db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    call = db.query(CallLog).filter(CallLog.id == call_id).first()
    if not call:
        raise HTTPException(status_code=404, detail="Call not found")
    ext = file.filename.split(".")[-1] if "." in file.filename else "webm"
    filename = f"call_{call_id}_{uuid.uuid4().hex[:8]}.{ext}"
    filepath = UPLOAD_DIR / filename
    content = await file.read()
    with open(filepath, "wb") as f:
        f.write(content)
    call.recording_path = str(filepath)
    db.commit()
    return {"recording_path": str(filepath), "file_size": len(content)}

@app.post("/api/calls/{call_id}/transcribe")
def transcribe_call(call_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    call = db.query(CallLog).filter(CallLog.id == call_id).first()
    if not call or not call.recording_path:
        raise HTTPException(status_code=404, detail="Call or recording not found")
    if not os.path.exists(call.recording_path):
        raise HTTPException(status_code=404, detail="Recording file not found")
    
    model = get_whisper()
    if not model:
        raise HTTPException(status_code=500, detail="Whisper model not available")
    
    try:
        segments, info = model.transcribe(call.recording_path, language="en")
        text = " ".join(seg.text for seg in segments)
        call.transcript = text
        db.commit()
        return {"transcript": text, "language": info.language, "duration": info.duration}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Transcription failed: {str(e)}")

# ── KPI Routes ──────────────────────────────────────────────────────────
@app.get("/api/kpis/dashboard")
def dashboard_kpis(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    today_start, tomorrow_start = utc_day_bounds()
    
    base = db.query(CallLog).filter(CallLog.call_time >= today_start, CallLog.call_time < tomorrow_start)
    if user.role != "admin":
        base = base.filter(CallLog.caller_id == user.id)
    
    total_dials = base.count()
    connects = base.filter(CallLog.disposition.in_(CONNECT_DISPOSITIONS)).count()
    dnc_count = base.filter(CallLog.disposition == "dnc").count()
    interested = base.filter(CallLog.disposition == "talked_interested").count()
    
    # By disposition
    disp_counts = {}
    for row in base.with_entities(CallLog.disposition, func.count(CallLog.id)).group_by(CallLog.disposition).all():
        disp_counts[row[0]] = row[1]
    
    # By hour
    hour_counts = []
    for h in range(6, 23):
        cnt = base.filter(extract('hour', CallLog.call_time) == h).count()
        if cnt > 0:
            hour_counts.append({"hour": h, "count": cnt})
    
    # Top callers
    if user.role == "admin":
        caller_stats = []
        try:
            caller_rows = db.query(
                CallLog.caller_id, User.full_name,
                func.count(CallLog.id).label("dials"),
            ).join(User, CallLog.caller_id == User.id)\
             .filter(CallLog.call_time >= today_start, CallLog.call_time < tomorrow_start)\
             .group_by(CallLog.caller_id).all()
            
            for row in caller_rows:
                cid = row.caller_id
                c_count = base.filter(
                    CallLog.caller_id == cid,
                    CallLog.disposition.in_(CONNECT_DISPOSITIONS)
                ).count()
                d = row.dials or 0
                caller_stats.append({
                    "name": row.full_name or f"Caller {cid}",
                    "dials": d,
                    "connects": c_count,
                    "connect_rate": round(c_count / d * 100, 1) if d > 0 else 0
                })
        except Exception as e:
            logger.error(f"Caller stats error: {e}")
            caller_stats = []
    else:
        caller_stats = []
    
    # Recent activity
    recent = []
    for c in base.order_by(CallLog.call_time.desc()).limit(20).all():
        caller_name = "Unknown"
        if c.caller:
            caller_name = c.caller.full_name or c.caller.username
        recent.append({
            "time": c.call_time.isoformat() if c.call_time else "",
            "caller_name": caller_name,
            "business_name": c.business_name,
            "disposition": DISPOSITION_LABELS.get(c.disposition, c.disposition),
            "duration": c.duration_seconds,
        })
    
    # Callers online (active today)
    callers_online = 0
    if user.role == "admin":
        active_caller_ids = db.query(CallLog.caller_id).filter(
            CallLog.call_time >= today_start, CallLog.call_time < tomorrow_start
        ).distinct().count()
        callers_online = active_caller_ids
    
    total = total_dials or 1
    return {
        "dials_today": total_dials,
        "connects_today": connects,
        "looms_sent_today": interested,
        "dnc_today": dnc_count,
        "connect_rate": round(connects / total * 100, 1),
        "dnc_rate": round(dnc_count / total * 100, 1),
        "loom_rate": round(interested / total * 100, 1),
        "callers_online": callers_online,
        "calls_by_disposition": disp_counts,
        "calls_by_hour": hour_counts,
        "top_callers": caller_stats,
        "recent_activity": recent,
    }

@app.get("/api/kpis/weekly")
def weekly_kpis(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    days = []
    base = db.query(CallLog)
    if user.role != "admin":
        base = base.filter(CallLog.caller_id == user.id)
    
    for i in range(6, -1, -1):
        d = datetime.utcnow().date() - timedelta(days=i)
        d_start = datetime(d.year, d.month, d.day)
        d_end = d_start + timedelta(days=1)
        day_data = base.filter(CallLog.call_time >= d_start, CallLog.call_time < d_end)
        days.append({
            "date": d.isoformat(),
            "day": d.strftime("%a"),
            "dials": day_data.count(),
            "connects": day_data.filter(CallLog.disposition.in_(CONNECT_DISPOSITIONS)).count(),
            "dnc": day_data.filter(CallLog.disposition == "dnc").count(),
        })
    return {"days": days}

@app.get("/api/kpis/caller/{caller_id}")
def caller_kpis(caller_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    if user.role != "admin" and user.id != caller_id:
        raise HTTPException(status_code=403, detail="Not authorized")
    today_start, tomorrow_start = utc_day_bounds()
    
    base = db.query(CallLog).filter(
        CallLog.caller_id == caller_id,
        CallLog.call_time >= today_start,
        CallLog.call_time < tomorrow_start
    )
    total = base.count()
    connects = base.filter(CallLog.disposition.in_(CONNECT_DISPOSITIONS)).count()
    dnc_count = base.filter(CallLog.disposition == "dnc").count()
    
    # Last 10 calls
    recent = [serialize(c) for c in base.order_by(CallLog.call_time.desc()).limit(10).all()]
    
    t = total or 1
    return {
        "dials_today": total,
        "connects_today": connects,
        "connect_rate": round(connects / t * 100, 1),
        "dnc_today": dnc_count,
        "dnc_rate": round(dnc_count / t * 100, 1),
        "recent_calls": recent,
    }

@app.get("/api/kpis/daily-summary")
def daily_summary(db: Session = Depends(get_db), user: User = Depends(require_admin)):
    today_start, tomorrow_start = utc_day_bounds()
    
    base = db.query(CallLog).filter(
        CallLog.call_time >= today_start, CallLog.call_time < tomorrow_start
    )
    
    total_dials = base.count()
    connects = base.filter(CallLog.disposition.in_(CONNECT_DISPOSITIONS)).count()
    dnc_count = base.filter(CallLog.disposition == "dnc").count()
    interested = base.filter(CallLog.disposition == "talked_interested").count()
    
    # Unique prospects contacted
    unique_prospects = db.query(func.count(func.distinct(CallLog.prospect_id))).filter(
        CallLog.call_time >= today_start, CallLog.call_time < tomorrow_start,
        CallLog.prospect_id != None
    ).scalar() or 0
    
    # Top caller
    top_caller_name = "N/A"
    top_caller_dials = 0
    top_row = db.query(
        CallLog.caller_id, User.full_name, func.count(CallLog.id).label("dials")
    ).join(User, CallLog.caller_id == User.id).filter(
        CallLog.call_time >= today_start, CallLog.call_time < tomorrow_start
    ).group_by(CallLog.caller_id).order_by(func.count(CallLog.id).desc()).first()
    if top_row:
        top_caller_name = top_row.full_name or f"Caller {top_row.caller_id}"
        top_caller_dials = top_row.dials or 0
    
    total = total_dials or 1
    connect_rate = round(connects / total * 100, 1)
    
    text_block = (
        f"Today: {total_dials} dials, {connects} connects ({connect_rate}%), "
        f"{interested} interested, {dnc_count} DNC. "
        f"Top caller: {top_caller_name} with {top_caller_dials} dials. "
        f"{unique_prospects} prospects contacted."
    )
    
    return {
        "total_dials": total_dials,
        "connects": connects,
        "connect_rate": connect_rate,
        "interested": interested,
        "dnc_count": dnc_count,
        "dnc_rate": round(dnc_count / total * 100, 1),
        "top_caller": {"name": top_caller_name, "dials": top_caller_dials},
        "unique_prospects_contacted": unique_prospects,
        "text_block": text_block,
    }

# ── Commission Routes ────────────────────────────────────────────────────
@app.get("/api/commissions")
def list_commissions(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    query = db.query(Commission)
    if user.role != "admin":
        query = query.filter(Commission.caller_id == user.id)
    return {"commissions": [serialize(c) for c in query.order_by(Commission.deal_date.desc()).all()]}

@app.post("/api/commissions")
def create_commission(data: dict, db: Session = Depends(get_db), user: User = Depends(require_admin)):
    caller_id = data.get("caller_id")
    if not caller_id:
        raise HTTPException(status_code=400, detail="caller_id required")
    caller = db.query(User).filter(User.id == caller_id).first()
    if not caller:
        raise HTTPException(status_code=404, detail="Caller not found")
    
    deal_amt = data.get("deal_amount", 1496.0)
    commission = Commission(
        caller_id=caller_id,
        prospect_id=data.get("prospect_id"),
        deal_amount=deal_amt,
        commission_amount=data.get("commission_amount", 100.0),
        notes=data.get("notes", ""),
    )
    db.add(commission)
    db.commit()
    db.refresh(commission)
    return serialize(commission)

@app.patch("/api/commissions/{comm_id}")
def update_commission(comm_id: int, data: dict, db: Session = Depends(get_db), user: User = Depends(require_admin)):
    comm = db.query(Commission).filter(Commission.id == comm_id).first()
    if not comm:
        raise HTTPException(status_code=404, detail="Commission not found")
    if data.get("status") == "paid":
        comm.status = "paid"
        comm.paid_date = datetime.utcnow()
    if "notes" in data:
        comm.notes = data["notes"]
    db.commit()
    db.refresh(comm)
    return serialize(comm)

@app.get("/api/commissions/summary")
def commission_summary(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    query = db.query(Commission).join(User, Commission.caller_id == User.id)
    if user.role != "admin":
        query = query.filter(Commission.caller_id == user.id)

    grouped = {}
    for commission in query.all():
        caller = commission.caller
        key = commission.caller_id
        if key not in grouped:
            grouped[key] = {
                "caller_id": key,
                "name": (caller.full_name or caller.username) if caller else f"Caller {key}",
                "total_deals": 0,
                "total_commission": 0.0,
                "paid_count": 0,
            }
        grouped[key]["total_deals"] += 1
        grouped[key]["total_commission"] += float(commission.commission_amount or 0)
        if commission.status == "paid":
            grouped[key]["paid_count"] += 1

    grand_total = 0
    summary = []
    for item in grouped.values():
        total = item["total_commission"]
        item["total_commission"] = round(total, 2)
        summary.append(item)
        grand_total += total
    
    return {"summary": summary, "grand_total": round(grand_total, 2)}

# ── Recording Routes ─────────────────────────────────────────────────────
@app.post("/api/recordings/upload")
async def upload_recording_file(file: UploadFile = File(...), call_id: int = Form(default=0), db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    ext = file.filename.split(".")[-1] if "." in file.filename else "webm"
    filename = f"rec_{user.id}_{uuid.uuid4().hex[:8]}.{ext}"
    filepath = UPLOAD_DIR / filename
    content = await file.read()
    with open(filepath, "wb") as f:
        f.write(content)
    
    rec = Recording(
        call_id=call_id or None,
        caller_id=user.id,
        file_path=str(filepath),
        file_size=len(content),
    )
    db.add(rec)
    db.commit()
    db.refresh(rec)
    return serialize(rec)

@app.get("/api/recordings/{rec_id}/play")
def play_recording(rec_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    rec = db.query(Recording).filter(Recording.id == rec_id).first()
    if not rec or not os.path.exists(rec.file_path):
        raise HTTPException(status_code=404, detail="Recording not found")
    return FileResponse(rec.file_path)

@app.post("/api/recordings/{rec_id}/transcribe")
def transcribe_recording(rec_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    rec = db.query(Recording).filter(Recording.id == rec_id).first()
    if not rec or not os.path.exists(rec.file_path):
        raise HTTPException(status_code=404, detail="Recording not found")
    
    model = get_whisper()
    if not model:
        raise HTTPException(status_code=500, detail="Whisper not available")
    
    try:
        segments, info = model.transcribe(rec.file_path, language="en")
        text = " ".join(seg.text for seg in segments)
        rec.transcript = text
        rec.transcribed = True
        db.commit()
        return {"transcript": text, "language": info.language, "duration": info.duration}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# ── Caller Management (Admin) ────────────────────────────────────────────
@app.get("/api/callers")
def list_callers(db: Session = Depends(get_db), user: User = Depends(require_admin)):
    callers = db.query(User).filter(User.role == "caller", User.active == True).all()
    result = []
    for c in callers:
        data = serialize(c)
        del data["pin_hash"]
        result.append(data)
    return {"callers": result}

@app.post("/api/callers")
def create_caller(data: dict, db: Session = Depends(get_db), user: User = Depends(require_admin)):
    username = data.get("username")
    pin = data.get("pin", "1234")
    full_name = data.get("full_name", "")
    if not username:
        raise HTTPException(status_code=400, detail="username required")
    existing = db.query(User).filter(User.username == username).first()
    if existing:
        raise HTTPException(status_code=400, detail="Username already exists")
    caller = User(username=username, pin_hash=hash_pin(pin), role="caller", full_name=full_name)
    db.add(caller)
    db.commit()
    db.refresh(caller)
    data = serialize(caller)
    del data["pin_hash"]
    return data

@app.get("/api/callers/{caller_id}/performance")
def caller_performance(caller_id: int, db: Session = Depends(get_db), user: User = Depends(require_admin)):
    caller = db.query(User).filter(User.id == caller_id).first()
    if not caller:
        raise HTTPException(status_code=404, detail="Caller not found")
    
    base = db.query(CallLog).filter(CallLog.caller_id == caller_id)
    
    total_dials = base.count()
    total_connects = base.filter(CallLog.disposition.in_(CONNECT_DISPOSITIONS)).count()
    total_dnc = base.filter(CallLog.disposition == "dnc").count()
    total_interested = base.filter(CallLog.disposition == "talked_interested").count()
    
    t = total_dials or 1
    avg_connect_rate = round(total_connects / t * 100, 1)
    
    recent_calls = [serialize(c) for c in base.order_by(CallLog.call_time.desc()).limit(20).all()]
    
    return {
        "caller_id": caller_id,
        "caller_name": caller.full_name or caller.username,
        "total_dials": total_dials,
        "total_connects": total_connects,
        "total_dnc": total_dnc,
        "total_interested": total_interested,
        "avg_connect_rate": avg_connect_rate,
        "recent_calls": recent_calls,
    }

# ── Client & Revenue Routes ─────────────────────────────────────────────
@app.get("/api/clients")
def list_clients(db: Session = Depends(get_db), user: User = Depends(require_admin)):
    clients = db.query(Client).order_by(Client.created_at.desc()).all()
    return {"clients": [serialize(c) for c in clients]}

@app.post("/api/clients")
def create_client(data: dict, db: Session = Depends(get_db), user: User = Depends(require_admin)):
    business_name = (data.get("business_name") or "").strip()
    if not business_name:
        raise HTTPException(status_code=400, detail="business_name required")
    client = Client(
        business_name=business_name,
        owner_name=data.get("owner_name", ""),
        phone=data.get("phone", ""),
        email=data.get("email", ""),
        city=data.get("city", ""),
        state=data.get("state", "TX"),
        onboarding_stage=data.get("onboarding_stage", "new"),
        subscription_status=data.get("subscription_status", "active"),
        setup_fee=float(data.get("setup_fee", 997.0) or 0),
        monthly_revenue=float(data.get("monthly_revenue", 499.0) or 0),
        usage_minutes=int(data.get("usage_minutes", 0) or 0),
        usage_rate=float(data.get("usage_rate", 0.5) or 0),
        notes=data.get("notes", ""),
    )
    db.add(client)
    db.commit()
    db.refresh(client)
    return serialize(client)

@app.patch("/api/clients/{client_id}")
def update_client(client_id: int, data: dict, db: Session = Depends(get_db), user: User = Depends(require_admin)):
    client = db.query(Client).filter(Client.id == client_id).first()
    if not client:
        raise HTTPException(status_code=404, detail="Client not found")
    for key, value in data.items():
        if hasattr(Client, key) and key != "id":
            setattr(client, key, value)
    if data.get("subscription_status") == "churned" and not client.churned_at:
        client.churned_at = datetime.utcnow()
    db.commit()
    db.refresh(client)
    return serialize(client)

@app.get("/api/revenue")
def revenue_metrics(db: Session = Depends(get_db), user: User = Depends(require_admin)):
    clients = db.query(Client).all()
    active = [c for c in clients if c.subscription_status == "active"]
    churned = [c for c in clients if c.subscription_status == "churned"]
    mrr = sum(float(c.monthly_revenue or 0) + float(c.usage_minutes or 0) * float(c.usage_rate or 0) for c in active)
    setup_total = sum(float(c.setup_fee or 0) for c in clients)
    commission_pending = db.query(func.sum(Commission.commission_amount)).filter(Commission.status != "paid").scalar() or 0
    commission_paid = db.query(func.sum(Commission.commission_amount)).filter(Commission.status == "paid").scalar() or 0
    total_clients = len(clients)
    churn_rate = round(len(churned) / total_clients * 100, 1) if total_clients else 0
    arpu = round(mrr / len(active), 2) if active else 0
    return {
        "mrr": round(mrr, 2),
        "active_clients": len(active),
        "total_clients": total_clients,
        "churned_clients": len(churned),
        "churn_rate": churn_rate,
        "arpu": arpu,
        "setup_revenue_total": round(setup_total, 2),
        "commission_pending": round(float(commission_pending), 2),
        "commission_paid": round(float(commission_paid), 2),
        "clients": [serialize(c) for c in clients],
    }

@app.get("/api/reports/daily")
def daily_report(db: Session = Depends(get_db), user: User = Depends(require_admin)):
    today_start, tomorrow_start = utc_day_bounds()
    calls = db.query(CallLog).filter(CallLog.call_time >= today_start, CallLog.call_time < tomorrow_start)
    dials = calls.count()
    connects = calls.filter(CallLog.disposition.in_(CONNECT_DISPOSITIONS)).count()
    interested = calls.filter(CallLog.disposition == "talked_interested").count()
    callbacks = calls.filter(CallLog.disposition == "callback_set").count()
    dnc = calls.filter(CallLog.disposition == "dnc").count()
    top = db.query(
        CallLog.caller_id, User.full_name, func.count(CallLog.id).label("dials")
    ).join(User, CallLog.caller_id == User.id).filter(
        CallLog.call_time >= today_start,
        CallLog.call_time < tomorrow_start,
    ).group_by(CallLog.caller_id).order_by(func.count(CallLog.id).desc()).first()
    top_line = f"Top caller: {(top.full_name or top.caller_id) if top else 'N/A'} with {top.dials if top else 0} dials."
    report = (
        f"LeadRescuePro Daily SDR Report - {today_start.date().isoformat()}\n"
        f"Dials: {dials}\nConnects: {connects}\nInterested/Loom permission: {interested}\n"
        f"Callbacks set: {callbacks}\nDNC: {dnc}\n{top_line}"
    )
    return {
        "date": today_start.date().isoformat(),
        "dials": dials,
        "connects": connects,
        "interested": interested,
        "callbacks": callbacks,
        "dnc": dnc,
        "text": report,
    }

# ── Health ───────────────────────────────────────────────────────────────
@app.get("/api/health")
def health():
    return {"status": "ok", "timestamp": datetime.utcnow().isoformat()}

# ── Leads Scrape ─────────────────────────────────────────────────────────
@app.post("/api/leads/scrape")
@app.post("/api/scraper/run")
def scrape_leads(data: dict, db: Session = Depends(get_db), user: User = Depends(require_admin)):
    city = (data.get("city") or "").strip()
    state = (data.get("state") or "TX").strip().upper()
    limit = int(data.get("limit", 30) or 30)
    if not city:
        raise HTTPException(status_code=400, detail="city required")

    scraper = PROJECT_DIR / "prospect-scraper.js"
    if not scraper.exists():
        raise HTTPException(status_code=500, detail="prospect-scraper.js not found")

    try:
        completed = subprocess.run(
            ["node", str(scraper), city, state, str(limit)],
            cwd=str(PROJECT_DIR),
            capture_output=True,
            text=True,
            timeout=90,
            check=False,
        )
    except FileNotFoundError:
        raise HTTPException(status_code=500, detail="Node.js is required to run the scraper")
    except subprocess.TimeoutExpired:
        raise HTTPException(status_code=504, detail="Scraper timed out")

    if completed.returncode != 0 and not completed.stdout.strip():
        raise HTTPException(status_code=500, detail=completed.stderr[-800:] or "Scraper failed")

    imported = 0
    reader = csv.DictReader(io.StringIO(completed.stdout))
    for row in reader:
        business_name = row.get("business_name") or row.get("name") or "Unknown"
        phone = row.get("phone", "")
        existing = db.query(Prospect).filter(Prospect.business_name == business_name, Prospect.phone == phone).first()
        if existing:
            continue
        prospect = Prospect(
            business_name=business_name,
            phone=phone,
            city=city,
            state=state,
            rating=float(row["rating"]) if row.get("rating") else None,
            reviews=int(float(row["reviews"])) if row.get("reviews") else None,
            website=row.get("website", ""),
            score=score_for_lead(row.get("rating"), row.get("reviews")),
            status="pending",
        )
        db.add(prospect)
        imported += 1
    db.commit()
    return {
        "status": "completed",
        "city": city,
        "state": state,
        "limit": limit,
        "imported": imported,
        "stderr": completed.stderr[-1200:],
    }

@app.post("/api/voiply/webhook")
async def voiply_webhook(request: Request, db: Session = Depends(get_db)):
    payload = await request.json()
    phone = payload.get("phone") or payload.get("from") or payload.get("caller_id") or ""
    business = payload.get("business_name") or payload.get("name") or "Voiply Call"
    disposition = payload.get("disposition") or "voicemail"
    admin = db.query(User).filter(User.role == "admin").first()
    if not admin:
        raise HTTPException(status_code=500, detail="No admin user configured")
    log = CallLog(
        caller_id=admin.id,
        business_name=business,
        phone=phone,
        disposition=disposition if disposition in DISPOSITION_LABELS else "voicemail",
        notes=json.dumps(payload)[:2000],
        duration_seconds=int(payload.get("duration_seconds", payload.get("duration", 0)) or 0),
    )
    db.add(log)
    db.commit()
    db.refresh(log)
    return {"ok": True, "call": serialize(log)}

# ── Serve Frontend ─────────────────────────────────────────────────────────
@app.get("/")
def serve_admin():
    return FileResponse(str(STATIC_DIR / "index.html"))

@app.get("/caller")
def serve_caller():
    return FileResponse(str(STATIC_DIR / "index.html"))

@app.get("/admin")
def serve_admin_page():
    return FileResponse(str(STATIC_DIR / "index.html"))

# ── Static files & Boot ──────────────────────────────────────────────────
if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("LRP_PORT", 8650))
    logger.info(f"Starting dashboard on port {port}")
    uvicorn.run("main:app", host="0.0.0.0", port=port, reload=False)
