"""Single-admin online control API reusing the actual TAZORIXX MVP27 engine.

No WhatsApp sending is done by this web process. Dedicated worker owns dispatch.
"""
from __future__ import annotations
import hashlib
import hmac
import os
import secrets
import sqlite3
import sys
import tempfile
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from urllib.request import Request, urlopen
from urllib.error import URLError
import json

from fastapi import FastAPI, Request, HTTPException, UploadFile, File, Depends, Form
from fastapi.responses import HTMLResponse, Response, JSONResponse
from pydantic import BaseModel, Field
from starlette.middleware.sessions import SessionMiddleware

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "engine"))
import core  # noqa: E402
import whatsapp_sender as sender  # noqa: E402

STUDIO_DIR = core.APP_DIR
DB_PATH = STUDIO_DIR / core.DB_NAME
TEMPLATE = ROOT / "engine" / "master_template.html"
EXPORT_DIR = STUDIO_DIR / "Website_Exports"
MAX_FILE_BYTES = 12 * 1024 * 1024
ENV = os.getenv("APP_ENV", "development").lower()
SESSION_SECRET = os.getenv("STUDIO_SESSION_SECRET", "")
ADMIN_PASSWORD = os.getenv("STUDIO_ADMIN_PASSWORD", "")
GATEWAY_URL = os.getenv("GATEWAY_INTERNAL_URL", "http://localhost:3030")
GATEWAY_API_KEY = os.getenv("GATEWAY_API_KEY", "")
GATEWAY_SESSION_ID = os.getenv("GATEWAY_SESSION_ID", "")

if ENV == "production" and (len(SESSION_SECRET) < 32 or len(ADMIN_PASSWORD) < 16):
    raise RuntimeError("Set strong STUDIO_SESSION_SECRET (32+ chars) and STUDIO_ADMIN_PASSWORD (16+ chars) before production")

app = FastAPI(title="TAZORIXX Unified Studio API", docs_url=None, redoc_url=None, openapi_url=None)
app.add_middleware(SessionMiddleware, secret_key=SESSION_SECRET or "development-only-change-me", https_only=ENV=="production", same_site="lax", max_age=12*3600)
login_errors: dict[str, list[float]] = {}

@contextmanager
def db():
    con = core.db_connect(DB_PATH)
    try:
        con.execute("PRAGMA busy_timeout=15000")
        sender.prepare_schema(con)
        sender.prepare_auto_schema(con)
        yield con
    finally:
        con.close()

def require_user(request: Request):
    if request.session.get("admin") is not True:
        raise HTTPException(status_code=401, detail="Please sign in")
    return True

def new_csrf() -> str:
    return secrets.token_urlsafe(24)

def require_write(request: Request):
    require_user(request)
    csrf = request.session.get("csrf") or ""
    if not csrf or not hmac.compare_digest(str(request.headers.get("x-csrf-token", "")), str(csrf)):
        raise HTTPException(403, "Invalid or missing CSRF token")
    return True

class LoginBody(BaseModel):
    password: str

class EditBody(BaseModel):
    name: str | None = Field(default=None, max_length=200)
    phone: str | None = Field(default=None, max_length=50)
    whatsapp: str | None = Field(default=None, max_length=50)
    email: str | None = Field(default=None, max_length=250)
    address: str | None = Field(default=None, max_length=500)
    tagline: str | None = Field(default=None, max_length=500)
    description: str | None = Field(default=None, max_length=5000)
    services: str | None = Field(default=None, max_length=5000)

class ConsentBody(BaseModel):
    recipient: str = Field(min_length=8, max_length=35)
    session_id: str = Field(min_length=1, max_length=100)
    consent_note: str = Field(min_length=10, max_length=1000)

class ScheduleBody(BaseModel):
    business_id: str
    date: str
    time: str

class RescheduleBody(BaseModel):
    date: str
    time: str
    expected_utc: str

class PauseBody(BaseModel):
    paused: bool

class ProfileBody(BaseModel):
    category: str = Field(max_length=100)
    headlines: list[str] = Field(min_length=1,max_length=20)
    introductions: list[str] = Field(min_length=1,max_length=20)

@app.middleware("http")
async def no_store(request: Request, call_next):
    response = await call_next(request)
    if request.url.path.startswith("/api/studio/"):
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
    return response

@app.get("/api/studio/health")
def health():
    return {"ok": True, "service": "TAZORIXX Studio", "sending_env_enabled": os.getenv("ENABLE_SENDING", "false").lower() == "true"}

@app.post("/api/studio/auth/login")
def login(body: LoginBody, request: Request):
    ip = request.client.host if request.client else "unknown"
    now = time.monotonic()
    bad = [t for t in login_errors.get(ip,[]) if now-t < 900]
    if len(bad) >= 5:
        raise HTTPException(429, "Too many attempts. Try later")
    if not ADMIN_PASSWORD or not hmac.compare_digest(body.password.encode(), ADMIN_PASSWORD.encode()):
        bad.append(now)
        login_errors[ip]=bad
        raise HTTPException(401, "Invalid credentials")
    login_errors.pop(ip, None)
    request.session.clear()
    request.session.update({"admin":True,"csrf":new_csrf()})
    return {"ok":True,"csrf":request.session["csrf"]}

@app.get("/api/studio/auth/me")
def me(request: Request, _: bool=Depends(require_user)):
    return {"ok":True, "csrf":request.session["csrf"]}

@app.post("/api/studio/auth/logout")
def logout(request:Request, _:bool=Depends(require_write)):
    request.session.clear()
    return {"ok":True}

@app.get("/api/studio/stats")
def stats(_:bool=Depends(require_user)):
    with db() as con:
        businesses = con.execute("SELECT count(*) FROM businesses").fetchone()[0]
        generated = con.execute("SELECT count(*) FROM generation").fetchone()[0]
        states = {r[0]:r[1] for r in con.execute("SELECT status,count(*) FROM whatsapp_demo_jobs GROUP BY status")}
        paused = sender.delivery_paused(con)
    return {"businesses":businesses,"generated":generated,"jobs":states,"paused":paused,"sending_env_enabled":os.getenv("ENABLE_SENDING", "false").lower()=="true"}

@app.get("/api/studio/businesses")
def businesses(search:str="", status:str="all", limit:int=200, _:bool=Depends(require_user)):
    if status not in ('all','missing','social_only','review','website_present'):
        raise HTTPException(400, "Invalid status")
    with db() as con:
        rows=core.list_businesses(con,search=search[:100],website=status,limit=max(1,min(limit,1000)))
        ids=[r['id'] for r in rows]
        gen_ids = {r[0] for r in con.execute('SELECT business_id FROM generation')}
        return [{"id":r['id'],"name":r['name'],"industry":r['industry'],"phone":r['phone'],"whatsapp":r['whatsapp'],"website_status":r['website_status'],"generated":r['id'] in gen_ids} for r in rows]

@app.get("/api/studio/businesses/{business_id}")
def business_detail(business_id:str, _:bool=Depends(require_user)):
    with db() as con:
        row=con.execute("SELECT * FROM businesses WHERE id=?",(business_id,)).fetchone()
        if not row: raise HTTPException(404,"Business not found")
        result = {k:row[k] for k in ('id','name','industry','subtype','phone','whatsapp','email','address','website_status','description','tagline','services','content_override','profile_override')}
        gen=con.execute("SELECT html_hash,exported_at FROM generation WHERE business_id=?",(business_id,)).fetchone()
        permission=con.execute("SELECT recipient,session_id,consent_note,enabled FROM whatsapp_demo_permissions WHERE business_id=?",(business_id,)).fetchone()
        result['generated']=gen is not None
        result['permission']=dict(permission) if permission else None
        result['images']=[dict(r) for r in core.list_approved_media(con,'business',business_id)]
        return result

@app.post("/api/studio/businesses/import")
async def import_excel(file:UploadFile=File(...), _:bool=Depends(require_write)):
    if not (file.filename or "").lower().endswith(".xlsx"):
        raise HTTPException(400,"Only .xlsx workbooks are supported")
    size=0
    with tempfile.TemporaryDirectory(prefix='tazorixx-excel-') as tmpdir:
        path=Path(tmpdir)/'upload.xlsx'
        try:
            with path.open('wb') as out:
                while True:
                    chunk=await file.read(512*1024)
                    if not chunk: break
                    size+=len(chunk)
                    if size>MAX_FILE_BYTES: raise HTTPException(413,"Excel upload must be 12MB or less")
                    out.write(chunk)
        finally: await file.close()
        try:
            with db() as con:
                result=core.import_workbook(con,path)
            return result
        except (ValueError,KeyError) as e:
            raise HTTPException(400,str(e)[:300]) from e

@app.post("/api/studio/businesses/{business_id}/edit")
def edit_business(business_id:str, body:EditBody, _:bool=Depends(require_write)):
    with db() as con:
        old=con.execute('SELECT profile_override FROM businesses WHERE id=?',(business_id,)).fetchone()
        if not old:
            raise HTTPException(404,"Business not found")
        core.update_override(con,business_id,body.model_dump(exclude_none=True),profile_slug=old['profile_override'])
    return {"ok":True,"note":"Regenerate website to apply edits"}

@app.post("/api/studio/businesses/{business_id}/generate")
def generate(business_id:str, _:bool=Depends(require_write)):
    with db() as con:
        try:
            path=core.export_business(con,business_id,TEMPLATE,EXPORT_DIR,overwrite=True)
        except (ValueError,FileNotFoundError) as e:
            raise HTTPException(400,str(e)[:500]) from e
    return {"ok":True,"filename":path.name,"preview":f"/api/studio/businesses/{business_id}/preview"}

@app.get("/api/studio/businesses/{business_id}/preview")
def preview(business_id:str, download:bool=False, _:bool=Depends(require_user)):
    with db() as con:
        try:
            _,path,_=sender.validated_export(con,business_id)
        except (ValueError,FileNotFoundError) as e: raise HTTPException(404,str(e)[:250]) from e
    content=path.read_bytes()
    headers={"Content-Security-Policy":"sandbox allow-scripts; default-src 'self' https: data: blob: 'unsafe-inline';", "X-Frame-Options":"SAMEORIGIN"}
    if download: headers['Content-Disposition']=f'attachment; filename="{path.name}"'
    return Response(content=content,media_type="text/html; charset=utf-8",headers=headers)

@app.post("/api/studio/businesses/{business_id}/consent")
def consent(business_id:str, body:ConsentBody, _:bool=Depends(require_write)):
    with db() as con:
        try:sender.save_permission(con,business_id,body.session_id,body.recipient,body.consent_note)
        except ValueError as e:raise HTTPException(400,str(e)[:250]) from e
    return {"ok":True}

@app.get("/api/studio/sessions")
def gateway_sessions(_:bool=Depends(require_user)):
    if not GATEWAY_API_KEY:
        return {"configured":False,"sessions":[]}
    try:
        resp=sender.sessions(GATEWAY_URL,GATEWAY_API_KEY)
    except Exception:
        return {"configured":True,"unreachable":True,"sessions":[]}
    return {"configured":True,"sessions":resp.get('data',[]) if isinstance(resp,dict) else []}

@app.post("/api/studio/schedules")
def add_schedule(body:ScheduleBody, _:bool=Depends(require_write)):
    if not GATEWAY_SESSION_ID:
        raise HTTPException(503,"Set GATEWAY_SESSION_ID on server before scheduling")
    with db() as con:
        perm=con.execute("SELECT recipient,session_id,consent_note,enabled FROM whatsapp_demo_permissions WHERE business_id=?",(body.business_id,)).fetchone()
        if not perm or not perm['enabled'] or perm['session_id'] != GATEWAY_SESSION_ID:
            raise HTTPException(400,"First record explicit recipient consent for this connected session")
        try: job=sender.schedule_job(con,body.business_id,perm['session_id'],perm['recipient'],f"{body.date} {body.time}",perm['consent_note'])
        except ValueError as e: raise HTTPException(400,str(e)[:300]) from e
    return {"ok":True,"job_id":job,"sending_env_enabled":os.getenv("ENABLE_SENDING", "false").lower()=="true"}

@app.get("/api/studio/schedules")
def schedules(limit:int=200, _:bool=Depends(require_user)):
    with db() as con:
        rows=[]
        for job,state,detail in sender.history_rows(con):
            rows.append({"id":job['id'],"name":job['name'],"business_id":job['business_id'],"recipient":job['recipient'],"schedule_utc":job['schedule_utc'],"status":job['status'],"state":state,"detail":detail,"updated_at":job['updated_at']})
            if len(rows)>=max(1,min(limit,1000)):break
        return rows

@app.post("/api/studio/schedules/{job_id}/reschedule")
def reschedule(job_id:int, body:RescheduleBody, _:bool=Depends(require_write)):
    with db() as con:
        try:sender.reschedule_job(con,job_id,f"{body.date} {body.time}",expected_utc=body.expected_utc)
        except ValueError as e: raise HTTPException(409,str(e)[:300]) from e
    return {"ok":True,"job_id":job_id}

@app.post("/api/studio/schedules/{job_id}/cancel")
def cancel(job_id:int, _:bool=Depends(require_write)):
    with db() as con:
        try:sender.cancel_job(con,job_id)
        except ValueError as e:raise HTTPException(409,str(e)[:300]) from e
    return {"ok":True,"job_id":job_id}

@app.post("/api/studio/sending/pause")
def pause(body:PauseBody, _:bool=Depends(require_write)):
    with db() as con:sender.set_delivery_paused(con,body.paused)
    return {"ok":True,"paused":body.paused,"env_enabled":os.getenv("ENABLE_SENDING", "false").lower()=="true"}

@app.exception_handler(Exception)
async def sanitized_errors(_request, error):
    # Intentionally never include raw exception details, paths or secrets in public responses.
    import logging
    logging.exception("Studio API unexpected error",exc_info=error)
    return JSONResponse({"detail":"Internal server error"},status_code=500)

@app.get("/api/studio/industries")
def list_industries(_:bool=Depends(require_user)):
    with db() as con:
        return [{"slug":r['slug'],"version":r['version'],"profile":json.loads(r['profile_json'])} for r in con.execute('SELECT slug,version,profile_json FROM profiles ORDER BY slug')]

@app.post("/api/studio/industries/{slug}")
def change_industry(slug:str, body:ProfileBody, _:bool=Depends(require_write)):
    with db() as con:
        rec=con.execute('SELECT profile_json FROM profiles WHERE slug=?',(slug,)).fetchone()
        if not rec:raise HTTPException(404,'Industry not found')
        profile=json.loads(rec['profile_json'])
        profile.update(body.model_dump())
        try:core.update_profile(con,slug,profile)
        except ValueError as e:raise HTTPException(400,str(e)[:300]) from e
    return {'ok':True,'note':'Profile updated. Regenerate affected websites to apply.'}

@app.post("/api/studio/businesses/{business_id}/images")
async def upload_image(business_id:str, request:Request, file:UploadFile=File(...), role:str=Form(...), rights_note:str=Form(...), rights_confirmed:str=Form(...)):
    require_write(request)
    if rights_confirmed != 'yes' or not rights_note.strip() or len(rights_note.strip())<8:
        raise HTTPException(400,'Confirm image rights and provide the rights note (8+ characters)')
    if role not in ('hero','gallery'):
        raise HTTPException(400,'Image role must be hero or gallery')
    filename=Path(file.filename or 'image').name
    if not filename.lower().endswith(('.png','.jpeg','.jpg','.webp','.gif','.bmp')):
        raise HTTPException(400,'Use a PNG/JPEG/WebP/GIF/BMP image')
    total=0
    with tempfile.TemporaryDirectory(prefix='tazorixx-image-') as tmpdir:
        path=Path(tmpdir)/filename
        try:
            with path.open('wb') as out:
                while True:
                    chunk=await file.read(512*1024)
                    if not chunk:break
                    total+=len(chunk)
                    if total>12_000_000:raise HTTPException(413,'Image is over 12 MB')
                    out.write(chunk)
        finally:await file.close()
        with db() as con:
            try:
                # Core handles conversion to optimized WebP, saved in SQLite with rights metadata.
                result=core.add_approved_media(con,'business',business_id,role,path,True,rights_note)
            except (ValueError,OSError) as e:raise HTTPException(400,str(e)[:300]) from e
        return {'ok':True,**result,'note':'Image saved. Regenerate website to apply.'}

class RemoveImageBody(BaseModel):
    role:str
    name:str

@app.post("/api/studio/businesses/{business_id}/images/delete")
def remove_image(business_id:str,body:RemoveImageBody, _:bool=Depends(require_write)):
    with db() as con:
        removed=core.remove_approved_media(con,'business',business_id,body.role,body.name)
        if not removed:raise HTTPException(404,'Image not found')
    return {'ok':True,'note':'Image removed. Regenerate website to apply.'}
