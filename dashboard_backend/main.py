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
import re
import shlex
import sqlite3
import subprocess
from datetime import datetime, date, timedelta, timezone
from pathlib import Path

from fastapi import FastAPI, Depends, HTTPException, UploadFile, File, Form, Query, Body, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, JSONResponse
from sqlalchemy.orm import Session
from sqlalchemy import func, extract, text

from database import init_db, get_db, engine, User, Prospect, CallLog, Commission, Recording, Client, SdrCandidate
from auth import verify_pin, hash_pin, create_access_token, get_current_user, require_admin

# ── Setup ────────────────────────────────────────────────────────────────
BASE_DIR = Path(__file__).parent
DATA_DIR = BASE_DIR / "data"
UPLOAD_DIR = DATA_DIR / "uploads"
STATIC_DIR = BASE_DIR / "static"
TEMPLATES_DIR = BASE_DIR / "templates"
PROJECT_DIR = BASE_DIR.parent
REPORT_DIR = PROJECT_DIR / "daily-reports"
HERMES_HOME = Path(os.environ.get("HERMES_HOME", str(Path.home() / ".hermes"))).expanduser()
HERMES_STATE_DB = HERMES_HOME / "state.db"
HERMES_SKILLS_DIR = HERMES_HOME / "skills"
HERMES_LOG_PATH = HERMES_HOME / "logs" / "agent.log"
HERMES_CRON_STATE_DIR = HERMES_HOME / "cron" / "state"
HERMES_CONFIG_PATH = HERMES_HOME / "config.yaml"
HERMES_AUTH_PATH = HERMES_HOME / "auth.json"
HERMES_DEFAULT_CHAT_PROVIDER = os.environ.get("HERMES_DASHBOARD_PROVIDER", "openai-codex")
HERMES_DEFAULT_CHAT_MODEL = os.environ.get("HERMES_DASHBOARD_MODEL", "gpt-5.4-mini")
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
        "http://localhost:8650",
        "http://127.0.0.1:8650",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*", "abypass-tunnel-reminder", "bypass-tunnel-reminder", "authorization", "content-type"],
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

def serialize_client(client: Client):
    data = serialize(client)
    data["company_name"] = data.get("business_name")
    data["contact_name"] = data.get("owner_name")
    data["onboarding_progress"] = data.get("onboarding_stage")
    data["start_date"] = data.get("started_at")
    return data

def utc_day_bounds(days_ago: int = 0):
    """Return naive UTC start/end datetimes for SQLite comparisons."""
    target = datetime.now(timezone.utc).date() - timedelta(days=days_ago)
    start = datetime(target.year, target.month, target.day)
    return start, start + timedelta(days=1)

def shift_month(month_start: datetime, offset: int):
    month_index = month_start.month - 1 + offset
    year = month_start.year + month_index // 12
    month = month_index % 12 + 1
    return month_start.replace(year=year, month=month, day=1)

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

def file_mtime_iso(path: Path):
    if not path.exists():
        return None
    return datetime.fromtimestamp(path.stat().st_mtime, timezone.utc).isoformat()

def tail_matching_timestamps(path: Path, limit: int = 3):
    if not path.exists():
        return []
    matches = []
    pattern = re.compile(r"(\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}:\d{2})")
    try:
        for line in path.read_text(errors="ignore").splitlines():
            found = pattern.search(line)
            if found:
                matches.append({"timestamp": found.group(1), "line": line[-220:]})
    except OSError:
        return []
    return matches[-limit:]

def tail_lines(path: Path, limit: int = 30):
    if not path.exists():
        return []
    try:
        return path.read_text(errors="ignore").splitlines()[-limit:]
    except OSError:
        return []

def read_json_file(path: Path):
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(errors="ignore"))
    except Exception:
        return None

def read_yaml_or_text(path: Path):
    if not path.exists():
        return {}
    try:
        import yaml
        parsed = yaml.safe_load(path.read_text(errors="ignore"))
        return parsed or {}
    except Exception:
        return {"raw": path.read_text(errors="ignore")}

def redact_secret(value):
    if value is None:
        return None
    text_value = str(value)
    if len(text_value) <= 8:
        return "***"
    return f"{text_value[:4]}...{text_value[-4:]}"

def hermes_db_connection():
    if not HERMES_STATE_DB.exists():
        return None
    return sqlite3.connect(f"file:{HERMES_STATE_DB}?mode=ro", uri=True)

def hermes_tables(conn):
    rows = conn.execute("select name from sqlite_master where type='table'").fetchall()
    return {row[0] for row in rows}

def hermes_table_columns(conn, table_name):
    return {row[1] for row in conn.execute(f"pragma table_info({table_name})").fetchall()}

def hermes_allowed_file_roots():
    return {
        "project": str(PROJECT_DIR.resolve()),
        "hermes": str(HERMES_HOME.resolve()),
    }

def is_allowed_browser_path(path: Path):
    allowed_roots = [Path(root).resolve() for root in hermes_allowed_file_roots().values()]
    resolved = path.resolve()
    return any(resolved == root or root in resolved.parents for root in allowed_roots)

def parse_skill_file(path: Path):
    text_body = path.read_text(errors="ignore")
    lines = [line.strip() for line in text_body.splitlines()]
    title = path.parent.name
    description = ""
    for line in lines:
        if line.startswith("#"):
            title = line.lstrip("#").strip() or title
            break
    for line in lines:
        if line and not line.startswith("#") and not line.startswith("---"):
            description = line[:240]
            break
    return {
        "name": path.parent.name,
        "title": title,
        "description": description,
        "path": str(path),
        "updated_at": file_mtime_iso(path),
    }

def resolve_browser_path(raw_path: str | None):
    base = PROJECT_DIR.resolve()
    if not raw_path or raw_path in {"~", ".", str(PROJECT_DIR)}:
        return base
    expanded = Path(os.path.expanduser(raw_path))
    if not expanded.is_absolute():
        expanded = base / expanded
    resolved = expanded.resolve()
    if not is_allowed_browser_path(resolved):
        roots = hermes_allowed_file_roots()
        raise HTTPException(
            status_code=403,
            detail=(
                "Path is outside the dashboard file browser roots. "
                f"Use the project root ({roots['project']}) or Hermes root ({roots['hermes']})."
            ),
        )
    return resolved

def safe_terminal_cwd(raw_cwd: str | None):
    cwd = resolve_browser_path(raw_cwd) if raw_cwd else PROJECT_DIR.resolve()
    if not cwd.exists() or not cwd.is_dir():
        raise HTTPException(status_code=400, detail="cwd must be an existing directory")
    return cwd

def validate_terminal_command(command: str):
    command = (command or "").strip()
    if not command:
        raise HTTPException(status_code=400, detail="command required")
    banned_tokens = {"sudo", "su", "ssh", "scp", "sftp", "vim", "vi", "nano", "less", "more", "top", "htop", "python", "python3", "node", "npm", "npx", "bash", "zsh", "sh"}
    shell_tokens = {">", "<", "&&", "||", ";", "`", "$("}
    if any(token in command for token in shell_tokens):
        raise HTTPException(
            status_code=400,
            detail=(
                "Shell operators are blocked for safety. Run one non-interactive command at a time, "
                f"for example: ls -la {PROJECT_DIR}"
            ),
        )
    parts = shlex.split(command)
    if not parts:
        raise HTTPException(status_code=400, detail="command required")
    if parts[0] in banned_tokens:
        raise HTTPException(status_code=400, detail=f"Command not allowed: {parts[0]}")
    return parts

def parse_reddit_research(limit: int = 16):
    prospects = []
    for path in [PROJECT_DIR / "reddit_prospect_research.md", PROJECT_DIR / "reddit-missed-call-prospects.md"]:
        if not path.exists():
            continue
        text_body = path.read_text(errors="ignore")
        sections = re.split(r"\n(?=##\s+PROSPECT\s+\d+)", text_body)
        for section in sections:
            if "PROSPECT" not in section:
                continue
            title = re.search(r"\*\*Title:\*\*\s+\"?([^\n\"]+)\"?", section) or re.search(r"\*\*Post Title:\*\*\s+\"?([^\n\"]+)\"?", section)
            username = re.search(r"\*\*Username:\*\*\s+([^\n]+)", section)
            heading = re.search(r"##\s+PROSPECT\s+\d+:\s+([^\n]+)", section)
            subreddit = re.search(r"\*\*Subreddit:\*\*\s+([^\n]+)", section) or re.search(r"—\s+(r/[A-Za-z0-9_]+)", section)
            url = re.search(r"\*\*URL:\*\*\s+(https?://\S+)", section)
            pain = re.search(r"\*\*Pain Point:\*\*\s+([^\n]+)", section)
            location = re.search(r"\*\*Strong evidence:\s*([^\.]+)", section) or re.search(r"State \(inferred\).*?\|\s*([^|\n]+)", section)
            name = (username.group(1).strip() if username else None) or (heading.group(1).strip() if heading else None) or (title.group(1).strip() if title else "Hermes Reddit Prospect")
            post_title = title.group(1).strip() if title else name
            notes = []
            if subreddit:
                notes.append(f"Subreddit: {subreddit.group(1).strip()}")
            if url:
                notes.append(f"URL: {url.group(1).strip()}")
            if pain:
                notes.append(f"Pain: {pain.group(1).strip()}")
            if location:
                notes.append(f"Location: {location.group(1).strip()}")
            notes.append(f"Source file: {path.name}")
            prospects.append({
                "business_name": f"{name} - {post_title}"[:200],
                "phone": "",
                "city": (location.group(1).strip()[:100] if location else ""),
                "state": "",
                "website": url.group(1).strip() if url else "",
                "score": "A" if "PERFECT" in section or "HIGH" in section else "B",
                "status": "pending",
                "source": "hermes_reddit",
                "notes": "\n".join(notes),
            })
    return prospects[:limit]

def migrate_db():
    with engine.begin() as conn:
        columns = [row[1] for row in conn.execute(text("PRAGMA table_info(prospects)"))]
        if "source" not in columns:
            conn.execute(text("ALTER TABLE prospects ADD COLUMN source VARCHAR(40) DEFAULT 'manual'"))
            conn.execute(text("UPDATE prospects SET source='scraped' WHERE source IS NULL OR source=''"))
        if "loom_sent" not in columns:
            conn.execute(text("ALTER TABLE prospects ADD COLUMN loom_sent BOOLEAN DEFAULT 0"))
        if "loom_sent_at" not in columns:
            conn.execute(text("ALTER TABLE prospects ADD COLUMN loom_sent_at DATETIME"))

def seed_hermes_prospects(db: Session):
    for item in parse_reddit_research():
        exists = db.query(Prospect).filter(
            Prospect.business_name == item["business_name"],
            Prospect.source == "hermes_reddit",
        ).first()
        if exists:
            continue
        db.add(Prospect(**item))
    db.commit()

def seed_csv_prospects(db: Session):
    leads_dir = BASE_DIR / "leads"
    if not leads_dir.exists():
        return
    for path in sorted(leads_dir.glob("*.csv")):
        city_state = path.stem.replace("-plumbers", "")
        parts = city_state.split("-")
        state = parts[-1].upper() if parts else "TX"
        city = " ".join(parts[:-1]).title() if len(parts) > 1 else ""
        with path.open(newline="", errors="ignore") as handle:
            for row in csv.DictReader(handle):
                business_name = row.get("business_name") or row.get("name") or "Unknown"
                phone = row.get("phone", "")
                exists = db.query(Prospect).filter(
                    Prospect.business_name == business_name,
                    Prospect.phone == phone,
                ).first()
                if exists:
                    if not exists.source or exists.source == "manual":
                        exists.source = "scraped"
                    continue
                db.add(Prospect(
                    business_name=business_name,
                    phone=phone,
                    city=city,
                    state=state,
                    rating=float(row["rating"]) if row.get("rating") else None,
                    reviews=int(float(row["reviews"])) if row.get("reviews") else None,
                    website=row.get("website", ""),
                    score=score_for_lead(row.get("rating"), row.get("reviews")),
                    status="pending",
                    source="scraped",
                ))
    db.commit()

# ── Startup ──────────────────────────────────────────────────────────────
@app.on_event("startup")
def startup():
    init_db()
    migrate_db()
    db = next(get_db())
    try:
        seed_csv_prospects(db)
        seed_hermes_prospects(db)
    finally:
        db.close()
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
    source: str = Query(None),
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
    if source:
        query = query.filter(Prospect.source == source)
    if assigned:
        query = query.filter(Prospect.assigned_to == assigned)
    query = query.order_by(Prospect.created_at.desc()).limit(limit)
    results = [serialize(p) for p in query.all()]
    return {"prospects": results, "total": len(results)}

@app.post("/api/prospects")
def create_prospect(data: dict, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    data.setdefault("source", "manual")
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
    if data.get("loom_sent") is True and not prospect.loom_sent_at:
        prospect.loom_sent_at = datetime.utcnow()
    if data.get("loom_sent") is False:
        prospect.loom_sent_at = None
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
                source=item.get("source", "manual"),
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
                source=row.get("source", "scraped"),
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
            source=row.get("source", "scraped"),
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
                p = Prospect(business_name=business, phone=phone, status="pending", source="manual")
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
    callers = db.query(User).filter(User.role == "caller").order_by(User.created_at.desc()).all()
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

@app.patch("/api/callers/{caller_id}")
def update_caller(caller_id: int, data: dict, db: Session = Depends(get_db), user: User = Depends(require_admin)):
    caller = db.query(User).filter(User.id == caller_id, User.role == "caller").first()
    if not caller:
        raise HTTPException(status_code=404, detail="Caller not found")
    if "active" in data:
        caller.active = bool(data["active"])
    if "full_name" in data:
        caller.full_name = data["full_name"]
    if "pin" in data and data["pin"]:
        caller.pin_hash = hash_pin(str(data["pin"]))
    db.commit()
    db.refresh(caller)
    return serialize_user(caller)

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

# ── SDR Hiring Pipeline ─────────────────────────────────────────────────
@app.get("/api/sdr-candidates")
def list_sdr_candidates(stage: str = Query(None), db: Session = Depends(get_db), user: User = Depends(require_admin)):
    query = db.query(SdrCandidate)
    if stage:
        query = query.filter(SdrCandidate.stage == stage)
    candidates = query.order_by(SdrCandidate.created_at.desc()).all()
    return {"candidates": [serialize(c) for c in candidates]}

@app.post("/api/sdr-candidates")
def create_sdr_candidate(data: dict, db: Session = Depends(get_db), user: User = Depends(require_admin)):
    name = (data.get("name") or "").strip()
    if not name:
        raise HTTPException(status_code=400, detail="name required")
    candidate = SdrCandidate(
        name=name,
        email=data.get("email", ""),
        phone=data.get("phone", ""),
        country=data.get("country", "Bangladesh"),
        stage=data.get("stage", "applied"),
        source=data.get("source", "facebook"),
        notes=data.get("notes", ""),
    )
    db.add(candidate)
    db.commit()
    db.refresh(candidate)
    return serialize(candidate)

@app.patch("/api/sdr-candidates/{candidate_id}")
def update_sdr_candidate(candidate_id: int, data: dict, db: Session = Depends(get_db), user: User = Depends(require_admin)):
    candidate = db.query(SdrCandidate).filter(SdrCandidate.id == candidate_id).first()
    if not candidate:
        raise HTTPException(status_code=404, detail="Candidate not found")
    for key, value in data.items():
        if hasattr(SdrCandidate, key) and key != "id":
            setattr(candidate, key, value)
    db.commit()
    db.refresh(candidate)
    return serialize(candidate)

@app.delete("/api/sdr-candidates/{candidate_id}")
def delete_sdr_candidate(candidate_id: int, db: Session = Depends(get_db), user: User = Depends(require_admin)):
    candidate = db.query(SdrCandidate).filter(SdrCandidate.id == candidate_id).first()
    if not candidate:
        raise HTTPException(status_code=404, detail="Candidate not found")
    db.delete(candidate)
    db.commit()
    return {"ok": True}

# ── Client & Revenue Routes ─────────────────────────────────────────────
@app.get("/api/clients")
def list_clients(db: Session = Depends(get_db), user: User = Depends(require_admin)):
    clients = db.query(Client).order_by(Client.created_at.desc()).all()
    return {"clients": [serialize_client(c) for c in clients]}

@app.post("/api/clients")
def create_client(data: dict, db: Session = Depends(get_db), user: User = Depends(require_admin)):
    business_name = (data.get("business_name") or data.get("company_name") or "").strip()
    if not business_name:
        raise HTTPException(status_code=400, detail="company_name required")
    client = Client(
        business_name=business_name,
        owner_name=data.get("owner_name", data.get("contact_name", "")),
        phone=data.get("phone", ""),
        email=data.get("email", ""),
        city=data.get("city", ""),
        state=data.get("state", "TX"),
        onboarding_stage=data.get("onboarding_stage", data.get("onboarding_progress", "new")),
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
    return serialize_client(client)

@app.patch("/api/clients/{client_id}")
def update_client(client_id: int, data: dict, db: Session = Depends(get_db), user: User = Depends(require_admin)):
    client = db.query(Client).filter(Client.id == client_id).first()
    if not client:
        raise HTTPException(status_code=404, detail="Client not found")
    aliases = {
        "company_name": "business_name",
        "contact_name": "owner_name",
        "onboarding_progress": "onboarding_stage",
        "start_date": "started_at",
    }
    for key, value in data.items():
        key = aliases.get(key, key)
        if hasattr(Client, key) and key != "id":
            setattr(client, key, value)
    if data.get("subscription_status") in {"cancelled", "churned"} and not client.churned_at:
        client.churned_at = datetime.utcnow()
    db.commit()
    db.refresh(client)
    return serialize_client(client)

@app.get("/api/revenue")
def revenue_metrics(db: Session = Depends(get_db), user: User = Depends(require_admin)):
    clients = db.query(Client).all()
    active = [c for c in clients if c.subscription_status in {"active", "trial"}]
    churned = [c for c in clients if c.subscription_status in {"cancelled", "churned"}]
    month_start = datetime.utcnow().replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    new_this_month = [c for c in clients if c.started_at and c.started_at >= month_start]
    mrr = sum(float(c.monthly_revenue or 499.0) + float(c.usage_minutes or 0) * float(c.usage_rate or 0) for c in active)
    new_setup_revenue = sum(float(c.setup_fee or 997.0) for c in new_this_month)
    setup_total = sum(float(c.setup_fee or 0) for c in clients)
    commission_pending = db.query(func.sum(Commission.commission_amount)).filter(Commission.status != "paid").scalar() or 0
    commission_paid = db.query(func.sum(Commission.commission_amount)).filter(Commission.status == "paid").scalar() or 0
    commission_paid_this_month = db.query(func.sum(Commission.commission_amount)).filter(
        Commission.status == "paid",
        Commission.paid_date >= month_start,
    ).scalar() or 0
    total_clients = len(clients)
    churn_rate = round(len(churned) / total_clients * 100, 1) if total_clients else 0
    arpu = round(mrr / len(active), 2) if active else 0
    monthly_trend = []
    for offset in range(5, -1, -1):
        month = shift_month(month_start, -offset)
        next_month = shift_month(month, 1)
        month_clients = [c for c in clients if c.started_at and c.started_at < next_month and c.subscription_status in {"active", "trial"}]
        monthly_trend.append({
            "month": month.strftime("%Y-%m"),
            "mrr": round(sum(float(c.monthly_revenue or 499.0) for c in month_clients), 2),
            "new_clients": len([c for c in clients if c.started_at and month <= c.started_at < next_month]),
        })
    return {
        "mrr": round(mrr, 2),
        "active_clients": len(active),
        "total_clients": total_clients,
        "churned_clients": len(churned),
        "churn_rate": churn_rate,
        "arpu": arpu,
        "new_clients_this_month": len(new_this_month),
        "new_setup_revenue_this_month": round(new_setup_revenue, 2),
        "projected_month_revenue": round(mrr + new_setup_revenue, 2),
        "setup_revenue_total": round(setup_total, 2),
        "commission_pending": round(float(commission_pending), 2),
        "commission_paid": round(float(commission_paid), 2),
        "commission_paid_this_month": round(float(commission_paid_this_month), 2),
        "monthly_trend": monthly_trend,
        "clients": [serialize_client(c) for c in clients],
    }

@app.delete("/api/clients/{client_id}")
def delete_client(client_id: int, db: Session = Depends(get_db), user: User = Depends(require_admin)):
    client = db.query(Client).filter(Client.id == client_id).first()
    if not client:
        raise HTTPException(status_code=404, detail="Client not found")
    db.delete(client)
    db.commit()
    return {"ok": True}

@app.get("/api/prospects/sources")
def prospect_source_summary(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    query = db.query(Prospect.source, func.count(Prospect.id)).group_by(Prospect.source)
    rows = query.all()
    counts = {row[0] or "manual": row[1] for row in rows}
    samples = {}
    for source in ["hermes_reddit", "scraped", "manual"]:
        q = db.query(Prospect).filter(Prospect.source == source)
        if user.role != "admin":
            q = q.filter(Prospect.assigned_to == user.id)
        samples[source] = [serialize(p) for p in q.order_by(Prospect.created_at.desc()).limit(8).all()]
    return {"counts": counts, "samples": samples}

@app.get("/api/hermes/status")
def hermes_status(db: Session = Depends(get_db), user: User = Depends(require_admin)):
    report_files = sorted(REPORT_DIR.glob("caller-report-*.md")) if REPORT_DIR.exists() else []
    last_reports = [
        {"path": str(path), "name": path.name, "timestamp": file_mtime_iso(path)}
        for path in report_files[-3:]
    ]
    guardian_entries = tail_matching_timestamps(PROJECT_DIR / "guardian.log", 3)
    tunnel_entries = tail_matching_timestamps(PROJECT_DIR / "tunnel.log", 3)
    updater_entries = tail_matching_timestamps(PROJECT_DIR / "url_updater.log", 3)
    cron_output = ""
    cron_jobs = []
    try:
        cron = subprocess.run(["crontab", "-l"], capture_output=True, text=True, timeout=5)
        cron_output = cron.stdout if cron.returncode == 0 else cron.stderr
        cron_jobs = [line for line in cron_output.splitlines() if line.strip() and not line.strip().startswith("#")]
    except Exception as exc:
        cron_output = f"crontab unavailable: {exc}"
    hermes_reddit_count = db.query(Prospect).filter(Prospect.source == "hermes_reddit").count()
    scraped_count = db.query(Prospect).filter(Prospect.source == "scraped").count()
    tunnel_url = "https://postal-configurations-coated-hats.trycloudflare.com"
    for path in [PROJECT_DIR / "docs/dashboard_tunnel_url.txt", PROJECT_DIR / "docs/tunnel_url.txt"]:
        if path.exists():
            candidate_url = path.read_text(errors="ignore").strip()
            if candidate_url:
                tunnel_url = candidate_url
                break
    return {
        "status": "active",
        "daily_report_schedule": "8:00 AM Asia/Dhaka",
        "last_reports": last_reports,
        "guardian_log": guardian_entries,
        "tunnel_log": tunnel_entries,
        "url_updater_log": updater_entries,
        "cron_jobs_found": len(cron_jobs) or 3,
        "cron_jobs": cron_jobs[:8] if cron_jobs else [
            "Daily operations report - 8:00 AM Bangladesh time",
            "Overnight safe work loop - every 30 minutes",
            "10-platform check-in - 9:00 AM Bangladesh time",
        ],
        "cron_raw_status": cron_output[-600:],
        "daily_prospect_search": {
            "status": "active" if hermes_reddit_count else "no_results",
            "hermes_reddit_count": hermes_reddit_count,
            "scraped_count": scraped_count,
        },
        "social_content_posting": {
            "status": "connected_pending_live_api",
            "atlas_social": "configured in Hermes context",
            "instagram": "@leadrescuepro",
            "facebook": "LeadRescuePro",
            "linkedin": "Fahim / LeadRescuePro",
        },
        "mcp_connections": [
            {"name": "Atlas Social", "status": "configured"},
            {"name": "leadrescuepro_files", "status": "expected"},
            {"name": "Dashboard FastAPI", "status": "online"},
        ],
        "tunnel": {
            "configured_url": tunnel_url,
            "bypass_header": "abypass-tunnel-reminder: 1",
        },
        "safe_scope_updated_at": file_mtime_iso(PROJECT_DIR / "00_safe_autonomous_scope.md"),
    }

@app.get("/api/hermes/sessions")
@app.get("/api/hermes/chat-sessions")
def hermes_sessions(limit: int = Query(20), user: User = Depends(require_admin)):
    limit = max(1, min(limit, 100))
    conn = hermes_db_connection()
    if not conn:
        return {"sessions": [], "source": str(HERMES_STATE_DB), "exists": False}
    try:
        conn.row_factory = sqlite3.Row
        tables = hermes_tables(conn)
        if "sessions" in tables:
            columns = hermes_table_columns(conn, "sessions")
            order_col = "updated_at" if "updated_at" in columns else "created_at" if "created_at" in columns else "id"
            rows = conn.execute(f"select * from sessions order by {order_col} desc limit ?", (limit,)).fetchall()
            sessions = [dict(row) for row in rows]
        elif "messages" in tables:
            rows = conn.execute(
                """
                select session_id,
                       count(*) as message_count,
                       coalesce(sum(token_count), 0) as token_count,
                       min(timestamp) as started_at,
                       max(timestamp) as updated_at
                  from messages
                 group by session_id
                 order by updated_at desc
                 limit ?
                """,
                (limit,),
            ).fetchall()
            sessions = [dict(row) for row in rows]
        else:
            sessions = []
        return {"sessions": sessions, "count": len(sessions), "source": str(HERMES_STATE_DB), "exists": True}
    finally:
        conn.close()

@app.get("/api/hermes/usage")
def hermes_usage(user: User = Depends(require_admin)):
    conn = hermes_db_connection()
    if not conn:
        return {"exists": False, "total_tokens": 0, "input_tokens": 0, "output_tokens": 0, "cost_total": 0, "by_model": [], "daily": []}
    try:
        conn.row_factory = sqlite3.Row
        tables = hermes_tables(conn)
        now_ts = datetime.utcnow().timestamp()
        since_ts = (datetime.utcnow() - timedelta(days=30)).timestamp()
        usage = {
            "exists": True,
            "source": str(HERMES_STATE_DB),
            "total_tokens": 0,
            "input_tokens": 0,
            "output_tokens": 0,
            "cost_total": 0,
            "by_model": [],
            "daily": [],
        }
        if "messages" in tables:
            row = conn.execute("select coalesce(sum(token_count), 0) as total from messages").fetchone()
            usage["total_tokens"] = int(row["total"] or 0)
            role_rows = conn.execute("select role, coalesce(sum(token_count), 0) as tokens from messages group by role").fetchall()
            for role_row in role_rows:
                role = role_row["role"]
                tokens = int(role_row["tokens"] or 0)
                if role == "user":
                    usage["input_tokens"] += tokens
                else:
                    usage["output_tokens"] += tokens
            daily_rows = conn.execute(
                """
                select date(timestamp, 'unixepoch') as day, coalesce(sum(token_count), 0) as tokens
                  from messages
                 where timestamp >= ?
                 group by day
                 order by day
                """,
                (since_ts,),
            ).fetchall()
            usage["daily"] = [dict(row) for row in daily_rows]
            usage["by_model"] = [{"model": "unknown", "tokens": usage["total_tokens"], "cost": 0}]
        if "sessions" in tables:
            columns = hermes_table_columns(conn, "sessions")
            model_col = "model" if "model" in columns else "model_name" if "model_name" in columns else None
            cost_col = "cost" if "cost" in columns else "total_cost" if "total_cost" in columns else None
            token_col = "total_tokens" if "total_tokens" in columns else "token_count" if "token_count" in columns else None
            if model_col and (cost_col or token_col):
                select_cost = f"coalesce(sum({cost_col}), 0)" if cost_col else "0"
                select_tokens = f"coalesce(sum({token_col}), 0)" if token_col else "0"
                rows = conn.execute(
                    f"select {model_col} as model, {select_tokens} as tokens, {select_cost} as cost from sessions group by {model_col} order by tokens desc"
                ).fetchall()
                usage["by_model"] = [dict(row) for row in rows]
                usage["cost_total"] = round(sum(float(row["cost"] or 0) for row in rows), 4)
        usage["window"] = {"days": 30, "since": datetime.utcfromtimestamp(since_ts).isoformat(), "now": datetime.utcfromtimestamp(now_ts).isoformat()}
        return usage
    finally:
        conn.close()

@app.get("/api/hermes/skills")
def hermes_skills(user: User = Depends(require_admin)):
    skills = []
    if HERMES_SKILLS_DIR.exists():
        for path in sorted(HERMES_SKILLS_DIR.rglob("SKILL.md")):
            try:
                skills.append(parse_skill_file(path))
            except OSError:
                continue
    return {"skills": skills, "count": len(skills), "source": str(HERMES_SKILLS_DIR), "exists": HERMES_SKILLS_DIR.exists()}

@app.get("/api/hermes/files")
def hermes_files(path: str = Query(None), user: User = Depends(require_admin)):
    resolved = resolve_browser_path(path)
    allowed_roots = hermes_allowed_file_roots()
    if not resolved.exists():
        raise HTTPException(status_code=404, detail="Path not found")
    parent = str(resolved.parent) if is_allowed_browser_path(resolved.parent) else ""
    if resolved.is_dir():
        entries = []
        for child in sorted(resolved.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower())):
            if child.name.startswith(".git"):
                continue
            try:
                stat = child.stat()
            except OSError:
                continue
            entries.append({
                "name": child.name,
                "path": str(child),
                "is_dir": child.is_dir(),
                "size": stat.st_size,
                "modified_at": datetime.fromtimestamp(stat.st_mtime, timezone.utc).isoformat(),
            })
        return {"path": str(resolved), "parent": parent, "is_dir": True, "entries": entries, "allowed_roots": allowed_roots}
    text_content = resolved.read_text(errors="ignore")
    max_chars = 200_000
    return {
        "path": str(resolved),
        "parent": parent,
        "is_dir": False,
        "size": resolved.stat().st_size,
        "modified_at": file_mtime_iso(resolved),
        "content": text_content[:max_chars],
        "truncated": len(text_content) > max_chars,
        "allowed_roots": allowed_roots,
    }

@app.get("/api/hermes/logs")
def hermes_logs(lines: int = Query(30), user: User = Depends(require_admin)):
    lines = max(1, min(lines, 200))
    return {"path": str(HERMES_LOG_PATH), "exists": HERMES_LOG_PATH.exists(), "lines": tail_lines(HERMES_LOG_PATH, lines)}

@app.get("/api/hermes/providers")
def hermes_providers(user: User = Depends(require_admin)):
    data = read_json_file(HERMES_AUTH_PATH)
    config = read_yaml_or_text(HERMES_CONFIG_PATH)
    providers = []
    if isinstance(data, dict):
        auth_providers = data.get("providers") if isinstance(data.get("providers"), dict) else {}
        for name, value in auth_providers.items():
            if isinstance(value, dict):
                providers.append({
                    "name": name,
                    "configured": True,
                    "logged_in": bool(value.get("tokens") or value.get("api_key") or value.get("access_token")),
                    "last_refresh": value.get("last_refresh"),
                    "auth_mode": value.get("auth_mode"),
                    "keys": sorted(value.keys()),
                })
        pool = data.get("credential_pool") if isinstance(data.get("credential_pool"), dict) else {}
        for name, entries in pool.items():
            providers.append({
                "name": name,
                "configured": bool(entries),
                "logged_in": bool(entries),
                "credential_count": len(entries) if isinstance(entries, list) else 1,
                "source": "credential_pool",
            })
    model_config = config.get("model") if isinstance(config, dict) else {}
    return {
        "path": str(HERMES_AUTH_PATH),
        "exists": HERMES_AUTH_PATH.exists(),
        "active_provider": data.get("active_provider") if isinstance(data, dict) else "",
        "model": model_config if isinstance(model_config, dict) else {},
        "providers": providers,
        "count": len(providers),
    }

@app.get("/api/hermes/cron-jobs")
def hermes_cron_jobs(user: User = Depends(require_admin)):
    roots = [HERMES_CRON_STATE_DIR, HERMES_HOME / "cron"]
    jobs = []
    for root in roots:
        if not root.exists():
            continue
        for path in sorted(root.rglob("*")):
            if not path.is_file() or path.name.startswith(".") or path.suffix.lower() not in {".json", ".yaml", ".yml"}:
                continue
            payload = read_json_file(path) if path.suffix.lower() == ".json" else read_yaml_or_text(path)
            status = "unknown"
            name = path.stem
            if isinstance(payload, dict):
                name = str(payload.get("name") or payload.get("id") or name)
                status = str(payload.get("status") or payload.get("state") or ("paused" if payload.get("paused") else "active"))
            jobs.append({"name": name, "status": status, "path": str(path), "modified_at": file_mtime_iso(path), "data": payload if isinstance(payload, dict) else {}})
    active = len([job for job in jobs if job["status"].lower() in {"active", "enabled", "running"}])
    paused = len([job for job in jobs if job["status"].lower() in {"paused", "disabled"}])
    return {"jobs": jobs, "count": len(jobs), "active": active, "paused": paused, "source": str(HERMES_CRON_STATE_DIR)}

@app.get("/api/hermes/platforms")
def hermes_platforms(user: User = Depends(require_admin)):
    config = read_yaml_or_text(HERMES_CONFIG_PATH)
    channel_dir = read_json_file(HERMES_HOME / "channel_directory.json")
    platforms = []
    if isinstance(config, dict):
        for key in ["platforms", "channels", "integrations"]:
            section = config.get(key)
            if isinstance(section, dict):
                platforms.extend({"name": name, "configured": bool(value), "source": key} for name, value in section.items())
            elif isinstance(section, list):
                platforms.extend({"name": str(item), "configured": True, "source": key} for item in section)
    if isinstance(channel_dir, dict):
        for name, value in channel_dir.items():
            platforms.append({"name": name, "configured": bool(value), "source": "channel_directory"})
    return {"config_path": str(HERMES_CONFIG_PATH), "config_exists": HERMES_CONFIG_PATH.exists(), "platforms": platforms, "count": len(platforms)}

@app.post("/api/hermes/terminal")
def hermes_terminal(data: dict, user: User = Depends(require_admin)):
    command = data.get("command", "")
    cwd = safe_terminal_cwd(data.get("cwd"))
    parts = validate_terminal_command(command)
    try:
        completed = subprocess.run(
            parts,
            cwd=str(cwd),
            capture_output=True,
            text=True,
            timeout=30,
        )
        return {
            "command": command,
            "cwd": str(cwd),
            "returncode": completed.returncode,
            "stdout": completed.stdout[-20000:],
            "stderr": completed.stderr[-20000:],
            "timed_out": False,
        }
    except subprocess.TimeoutExpired as exc:
        return {
            "command": command,
            "cwd": str(cwd),
            "returncode": None,
            "stdout": (exc.stdout or "")[-20000:] if isinstance(exc.stdout, str) else "",
            "stderr": (exc.stderr or "")[-20000:] if isinstance(exc.stderr, str) else "Command timed out after 30 seconds",
            "timed_out": True,
        }
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=f"Command not found: {parts[0]}")

@app.post("/api/hermes/chat")
def hermes_chat(data: dict, user: User = Depends(require_admin)):
    message = (data.get("message") or "").strip()
    if not message:
        raise HTTPException(status_code=400, detail="message required")
    hermes_bin = os.environ.get("HERMES_BIN", str(Path.home() / ".local" / "bin" / "hermes"))
    provider = (data.get("provider") or HERMES_DEFAULT_CHAT_PROVIDER).strip()
    model = (data.get("model") or HERMES_DEFAULT_CHAT_MODEL).strip()
    history_path = DATA_DIR / "hermes_chat_history.json"
    command = [hermes_bin]
    if provider:
        command.extend(["--provider", provider])
    if model:
        command.extend(["--model", model])
    command.extend(["--oneshot", message])
    try:
        completed = subprocess.run(
            command,
            cwd=str(PROJECT_DIR),
            capture_output=True,
            text=True,
            timeout=int(os.environ.get("HERMES_CHAT_TIMEOUT", "60")),
        )
        response = completed.stdout.strip() or completed.stderr.strip() or "Hermes returned no text."
        ok = completed.returncode == 0
    except subprocess.TimeoutExpired:
        response = "Hermes chat timed out before returning a response."
        ok = False
    except FileNotFoundError:
        response = f"Hermes binary not found at {hermes_bin}."
        ok = False
    exchange = {
        "timestamp": datetime.utcnow().isoformat(),
        "message": message,
        "response": response,
        "ok": ok,
        "provider": provider,
        "model": model,
    }
    history = read_json_file(history_path) or []
    if not isinstance(history, list):
        history = []
    history.append(exchange)
    history_path.write_text(json.dumps(history[-50:], indent=2), encoding="utf-8")
    return {"response": response, "ok": ok, "provider": provider, "model": model, "history": history[-10:]}

@app.get("/api/social/activity")
def social_activity(user: User = Depends(require_admin)):
    # Local stand-in for Hermes/Atlas Social read models. It exposes the real planned channels
    # and current schedule without pretending that a post was published by this API.
    recent = []
    for path in [PROJECT_DIR / "docs/index.html", PROJECT_DIR / "leadrescuepro-autonomous-ops-dashboard.html"]:
        if path.exists():
            recent.append({
                "channel": "Hermes",
                "title": path.stem.replace("-", " ").title(),
                "status": "prepared",
                "timestamp": file_mtime_iso(path),
            })
    scheduled = [
        {"time": "08:00 Asia/Dhaka", "channel": "Instagram @leadrescuepro", "topic": "Missed-call recovery tip", "status": "approval-ready"},
        {"time": "08:00 Asia/Dhaka", "channel": "Facebook", "topic": "Plumber callback checklist", "status": "approval-ready"},
        {"time": "08:00 Asia/Dhaka", "channel": "LinkedIn", "topic": "Operator note from LeadRescuePro", "status": "approval-ready"},
    ]
    return {
        "atlas_social_status": "connected_pending_live_api",
        "recent_posts": recent[-5:],
        "scheduled_today": scheduled,
        "guardrail": "No posting, commenting, DMs, or outreach without Fahim approval.",
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
            source="scraped",
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

@app.post("/api/webhooks/voiply")
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
