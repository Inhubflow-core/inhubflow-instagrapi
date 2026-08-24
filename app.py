import os
import time
import logging
from typing import List, Optional
from fastapi import FastAPI, HTTPException, Header, Depends
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from instagrapi import Client
from instagrapi.types import UserShort, User

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("InHubFlow-Instagrapi")

app = FastAPI(
    title="InHubFlow Instagram Prospecting & Automation API",
    description="Microservicio REST para extracción de leads, scraping de seguidores y automatización de DMs en Instagram",
    version="1.0.0"
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

API_SECRET = os.getenv("AUTHENTICATION_API_KEY", "inhubflow_ig_secret_key_2026")

# In-memory client storage (instance_id -> Client)
clients = {}

def get_client(account_id: str = "default") -> Client:
    if account_id not in clients:
        cl = Client()
        cl.delay_range = [2, 5]
        clients[account_id] = cl
    return clients[account_id]

def verify_token(api_key: Optional[str] = Header(None, alias="apikey")):
    if api_key != API_SECRET:
        raise HTTPException(status_code=401, detail="API Key no autorizada o inválida")
    return True

# --- DTO Models ---
class LoginRequest(BaseModel):
    account_id: str = "default"
    username: str
    password: str
    verification_code: Optional[str] = None
    proxy: Optional[str] = None

class ExtractFollowersRequest(BaseModel):
    account_id: str = "default"
    target_username: str
    amount: int = 100
    filter_business_only: bool = False

class ExtractLikersRequest(BaseModel):
    account_id: str = "default"
    media_url_or_code: str
    amount: int = 100

class SendDMRequest(BaseModel):
    account_id: str = "default"
    recipient_usernames: List[str]
    message_text: str
    delay_seconds: int = 10

@app.get("/")
def root():
    return {
        "status": 200,
        "service": "InHubFlow Instagram Automation Gateway",
        "version": "1.0.0",
        "docs": "/docs"
    }

@app.get("/health")
def health():
    return {"status": "ok", "clients_active": len(clients)}

@app.post("/api/auth/login", dependencies=[Depends(verify_token)])
def login_instagram(req: LoginRequest):
    try:
        cl = get_client(req.account_id)
        if req.proxy:
            cl.set_proxy(req.proxy)
        
        session_file = f"/tmp/session_{req.account_id}.json"
        if os.path.exists(session_file):
            try:
                cl.load_settings(session_file)
            except Exception:
                pass
        
        login_result = cl.login(req.username, req.password, verification_code=req.verification_code)
        cl.dump_settings(session_file)
        
        return {
            "status": "success",
            "message": f"Sesión iniciada correctamente para @{req.username}",
            "user_id": cl.user_id
        }
    except Exception as e:
        logger.error(f"Login error: {str(e)}")
        raise HTTPException(status_code=400, detail=str(e))

@app.post("/api/extract/followers", dependencies=[Depends(verify_token)])
def extract_followers(req: ExtractFollowersRequest):
    try:
        cl = get_client(req.account_id)
        target_user_id = cl.user_id_from_username(req.target_username)
        
        followers = cl.user_followers(target_user_id, amount=req.amount)
        leads = []
        
        for user_id, user_short in followers.items():
            lead_data = {
                "id": str(user_id),
                "username": user_short.username,
                "full_name": user_short.full_name,
                "profile_pic_url": str(user_short.profile_pic_url) if user_short.profile_pic_url else None,
                "is_private": user_short.is_private
            }
            leads.append(lead_data)
            
        return {
            "status": "success",
            "target": req.target_username,
            "total_extracted": len(leads),
            "leads": leads
        }
    except Exception as e:
        logger.error(f"Followers extraction error: {str(e)}")
        raise HTTPException(status_code=400, detail=str(e))

@app.post("/api/extract/likers", dependencies=[Depends(verify_token)])
def extract_likers(req: ExtractLikersRequest):
    try:
        cl = get_client(req.account_id)
        media_pk = cl.media_pk_from_url(req.media_url_or_code) if "http" in req.media_url_or_code else req.media_url_or_code
        likers = cl.media_likers(media_pk)
        
        leads = []
        for user in likers[:req.amount]:
            leads.append({
                "id": str(user.pk),
                "username": user.username,
                "full_name": user.full_name,
                "profile_pic_url": str(user.profile_pic_url) if user.profile_pic_url else None
            })
            
        return {
            "status": "success",
            "media_pk": str(media_pk),
            "total_extracted": len(leads),
            "leads": leads
        }
    except Exception as e:
        logger.error(f"Likers extraction error: {str(e)}")
        raise HTTPException(status_code=400, detail=str(e))

@app.post("/api/messages/send-dm", dependencies=[Depends(verify_token)])
def send_direct_message(req: SendDMRequest):
    try:
        cl = get_client(req.account_id)
        results = []
        
        for username in req.recipient_usernames:
            try:
                recipient_id = cl.user_id_from_username(username)
                sent = cl.direct_send(req.message_text, user_ids=[recipient_id])
                results.append({"username": username, "status": "sent", "thread_id": sent.thread_id})
                time.sleep(req.delay_seconds)
            except Exception as item_error:
                results.append({"username": username, "status": "failed", "error": str(item_error)})
                
        return {
            "status": "completed",
            "total_processed": len(results),
            "results": results
        }
    except Exception as e:
        logger.error(f"DM sending error: {str(e)}")
        raise HTTPException(status_code=400, detail=str(e))

if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", 8000))
    uvicorn.run("app:app", host="0.0.0.0", port=port, reload=False)
