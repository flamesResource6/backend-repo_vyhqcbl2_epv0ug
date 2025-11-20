import os
from typing import List, Optional, Dict, Any
from datetime import datetime, timedelta

from fastapi import FastAPI, HTTPException, Request, Depends
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel, EmailStr

from database import db, create_document, get_documents
from schemas import (
    Lead, ChatMessage, Booking, SupportTicket, PaymentRecord,
    SmsMessage, CallLog,
    AuthUser, Organization, Membership
)

# Twilio SDK
from twilio.rest import Client as TwilioClient
from twilio.twiml.messaging_response import MessagingResponse
from twilio.twiml.voice_response import VoiceResponse, Gather
from twilio.request_validator import RequestValidator

# JWT + password hashing
import jwt
import hashlib
import secrets
from bson import ObjectId

app = FastAPI(title="AHC Front Desk Assistant API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ------------------------------------------------------
# Auth helpers (password hashing + JWT)
# ------------------------------------------------------
SECRET_KEY = os.getenv("SECRET_KEY", "dev-secret-change-me")
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = int(os.getenv("ACCESS_TOKEN_EXPIRE_MINUTES", "43200"))  # 30 days default


def hash_password(password: str, salt: Optional[str] = None) -> Dict[str, str]:
    salt = salt or secrets.token_hex(16)
    pwd_hash = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        bytes.fromhex(salt),
        100_000,
        dklen=32,
    ).hex()
    return {"salt": salt, "hash": pwd_hash}


def verify_password(password: str, salt: str, expected_hash: str) -> bool:
    return hash_password(password, salt)["hash"] == expected_hash


def create_access_token(data: dict, expires_delta: Optional[timedelta] = None) -> str:
    to_encode = data.copy()
    expire = datetime.utcnow() + (expires_delta or timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES))
    to_encode.update({"exp": expire})
    return jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: Dict[str, Any]


async def get_current_user(request: Request) -> Dict[str, Any]:
    auth_header = request.headers.get("Authorization", "")
    if not auth_header.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Not authenticated")
    token = auth_header.split(" ", 1)[1]
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
    except Exception:
        raise HTTPException(status_code=401, detail="Invalid token")

    email = payload.get("sub")
    if not email:
        raise HTTPException(status_code=401, detail="Invalid token payload")

    users = get_documents("authuser", {"email": email}, 1)
    if not users:
        raise HTTPException(status_code=401, detail="User not found")

    user = users[0]
    user["_id"] = str(user.get("_id"))
    # remove sensitive
    user.pop("password_hash", None)
    user.pop("password_salt", None)
    return user


# ------------------------------------------------------
# Twilio helpers
# ------------------------------------------------------

def get_twilio_client():
    account_sid = os.getenv("TWILIO_ACCOUNT_SID")
    auth_token = os.getenv("TWILIO_AUTH_TOKEN")
    if not account_sid or not auth_token:
        raise HTTPException(status_code=500, detail="Twilio credentials not configured")
    return TwilioClient(account_sid, auth_token)


def validate_twilio_request(request: Request, form_dict: dict) -> bool:
    """Validate X-Twilio-Signature when enabled.
    Set TWILIO_VALIDATE_SIGNATURE=true to enforce validation.
    Uses TWILIO_AUTH_TOKEN.
    """
    enforce = os.getenv("TWILIO_VALIDATE_SIGNATURE", "false").lower() == "true"
    auth_token = os.getenv("TWILIO_AUTH_TOKEN")
    if not enforce or not auth_token:
        return True

    signature = request.headers.get("X-Twilio-Signature")
    if not signature:
        return False

    validator = RequestValidator(auth_token)

    # Try to validate using the exact URL Twilio called
    url_used = str(request.url)
    valid = validator.validate(url_used, form_dict, signature)
    if valid:
        return True

    # Fallback: validate against PUBLIC_BACKEND_URL + path (useful behind proxies)
    base_url = os.getenv("PUBLIC_BACKEND_URL")
    if base_url:
        alt_url = base_url.rstrip("/") + str(request.url.path)
        return validator.validate(alt_url, form_dict, signature)
    return False


@app.get("/")
def read_root():
    return {"message": "AHC Front Desk Assistant API running"}


@app.get("/test")
def test_database():
    response = {
        "backend": "✅ Running",
        "database": "❌ Not Available",
        "database_url": None,
        "database_name": None,
        "connection_status": "Not Connected",
        "collections": []
    }

    try:
        if db is not None:
            response["database"] = "✅ Available"
            response["database_url"] = "✅ Configured"
            response["database_name"] = getattr(db, 'name', '✅ Connected')
            response["connection_status"] = "Connected"
            try:
                collections = db.list_collection_names()
                response["collections"] = collections[:10]
                response["database"] = "✅ Connected & Working"
            except Exception as e:
                response["database"] = f"⚠️  Connected but Error: {str(e)[:50]}"
        else:
            response["database"] = "⚠️  Available but not initialized"
    except Exception as e:
        response["database"] = f"❌ Error: {str(e)[:50]}"

    response["database_url"] = "✅ Set" if os.getenv("DATABASE_URL") else "❌ Not Set"
    response["database_name"] = "✅ Set" if os.getenv("DATABASE_NAME") else "❌ Not Set"

    # Twilio env presence
    response["twilio_account_sid"] = "✅ Set" if os.getenv("TWILIO_ACCOUNT_SID") else "❌ Not Set"
    response["twilio_auth_token"] = "✅ Set" if os.getenv("TWILIO_AUTH_TOKEN") else "❌ Not Set"
    response["twilio_phone_number"] = "✅ Set" if os.getenv("TWILIO_PHONE_NUMBER") else "❌ Not Set"
    response["twilio_validate_signature"] = os.getenv("TWILIO_VALIDATE_SIGNATURE", "false")

    # Auth presence
    response["auth_secret_key"] = "✅ Set" if os.getenv("SECRET_KEY") else "⚠️ Using default"

    return response

# ------------------------------------------------------
# Authentication & Users
# ------------------------------------------------------

class SignupRequest(BaseModel):
    name: str
    email: EmailStr
    password: str


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


@app.post("/auth/signup", response_model=TokenResponse)
async def auth_signup(req: SignupRequest):
    existing = get_documents("authuser", {"email": req.email.lower()}, 1)
    if existing:
        raise HTTPException(status_code=400, detail="User already exists")

    hp = hash_password(req.password)
    user_doc = {
        "name": req.name,
        "email": req.email.lower(),
        "password_hash": hp["hash"],
        "password_salt": hp["salt"],
        "is_active": True,
    }
    _id = create_document("authuser", user_doc)

    token = create_access_token({"sub": req.email.lower()})
    safe_user = {"id": _id, "name": req.name, "email": req.email.lower()}
    return TokenResponse(access_token=token, user=safe_user)


@app.post("/auth/login", response_model=TokenResponse)
async def auth_login(req: LoginRequest):
    users = get_documents("authuser", {"email": req.email.lower()}, 1)
    if not users:
        raise HTTPException(status_code=400, detail="Invalid credentials")
    user = users[0]
    if not verify_password(req.password, user.get("password_salt", ""), user.get("password_hash", "")):
        raise HTTPException(status_code=400, detail="Invalid credentials")

    token = create_access_token({"sub": user["email"]})
    safe_user = {"id": str(user.get("_id")), "name": user.get("name"), "email": user.get("email")}
    return TokenResponse(access_token=token, user=safe_user)


@app.get("/auth/me")
async def auth_me(current_user: Dict[str, Any] = Depends(get_current_user)):
    return current_user


# ------------------------------------------------------
# Organizations & Memberships
# ------------------------------------------------------

class OrgCreateRequest(BaseModel):
    name: str


@app.post("/orgs")
async def create_org(req: OrgCreateRequest, current_user: Dict[str, Any] = Depends(get_current_user)):
    org = {
        "name": req.name,
        "owner_email": current_user["email"],
    }
    org_id = create_document("organization", org)

    create_document("membership", {
        "user_email": current_user["email"],
        "org_id": org_id,
        "role": "admin",
    })

    return {"id": org_id, "name": req.name}


@app.get("/orgs/mine")
async def list_my_orgs(current_user: Dict[str, Any] = Depends(get_current_user)):
    memberships = get_documents("membership", {"user_email": current_user["email"]}, 100)
    org_ids = [m.get("org_id") for m in memberships if m.get("org_id")]
    if not org_ids:
        return []

    # Convert to ObjectId list for query
    try:
        oids = [ObjectId(oid) for oid in org_ids]
    except Exception:
        oids = []
    orgs = get_documents("organization", {"_id": {"$in": oids}}, 100)
    for o in orgs:
        o["_id"] = str(o.get("_id"))
    return orgs


# ------------------------------------------------------
# Lead capture & qualification
# ------------------------------------------------------

@app.post("/leads")
async def create_lead(lead: Lead):
    lead_id = create_document("lead", lead)
    return {"id": lead_id, "status": "saved"}


@app.get("/leads")
async def list_leads(limit: int = 100):
    docs = get_documents("lead", {}, limit)
    # Convert ObjectId to string
    for d in docs:
        d["_id"] = str(d.get("_id"))
    return docs

# ------------------------------------------------------
# Chat logging
# ------------------------------------------------------

@app.post("/chats")
async def add_chat_message(msg: ChatMessage):
    msg_id = create_document("chatmessage", msg)
    return {"id": msg_id}


@app.get("/chats")
async def list_chats(limit: int = 200):
    docs = get_documents("chatmessage", {}, limit)
    for d in docs:
        d["_id"] = str(d.get("_id"))
    return docs

# ------------------------------------------------------
# Demo booking
# ------------------------------------------------------

@app.post("/bookings")
async def create_booking(booking: Booking):
    booking_id = create_document("booking", booking)
    # In a real app, send confirmation email + calendar invite here
    return {"id": booking_id, "status": "scheduled"}


@app.get("/bookings")
async def list_bookings(limit: int = 100):
    docs = get_documents("booking", {}, limit)
    for d in docs:
        d["_id"] = str(d.get("_id"))
    return docs

# ------------------------------------------------------
# Support tickets routing
# ------------------------------------------------------

@app.post("/tickets")
async def create_ticket(ticket: SupportTicket):
    ticket_id = create_document("supportticket", ticket)
    # In a real app: auto-assign based on issue_type and notify team
    return {"id": ticket_id, "status": "created"}


@app.get("/tickets")
async def list_tickets(limit: int = 100):
    docs = get_documents("supportticket", {}, limit)
    for d in docs:
        d["_id"] = str(d.get("_id"))
    return docs

# ------------------------------------------------------
# Payments (Stripe placeholder flow)
# ------------------------------------------------------

class CheckoutRequest(BaseModel):
    name: Optional[str] = None
    email: Optional[str] = None
    plan: str
    amount_cents: int


@app.post("/checkout")
async def create_checkout(req: CheckoutRequest):
    # Placeholder: simulate a Stripe Checkout session creation
    # Store initiated payment record
    record = PaymentRecord(
        name=req.name,
        email=req.email,
        plan=req.plan,
        amount_cents=req.amount_cents,
        status="initiated",
        provider="stripe",
        checkout_session_id="sess_mock_123"
    )
    rec_id = create_document("paymentrecord", record)
    return {
        "checkout_url": "https://checkout.stripe.com/pay/mock-session",
        "session_id": "sess_mock_123",
        "record_id": rec_id
    }


@app.post("/checkout/confirm/{session_id}")
async def confirm_checkout(session_id: str):
    # Placeholder confirmation; in real use, verify via webhook
    create_document("paymentrecord", {
        "session_id": session_id,
        "status": "succeeded",
        "provider": "stripe"
    })
    return {"status": "succeeded", "session_id": session_id}


@app.get("/payments")
async def list_payments(limit: int = 100):
    docs = get_documents("paymentrecord", {}, limit)
    for d in docs:
        d["_id"] = str(d.get("_id"))
    return docs

# ------------------------------------------------------
# CSV export for analytics
# ------------------------------------------------------

@app.get("/export/{resource}")
async def export_csv(resource: str, limit: int = 1000):
    import csv
    import io

    collection_map = {
        "leads": "lead",
        "chats": "chatmessage",
        "bookings": "booking",
        "tickets": "supportticket",
        "payments": "paymentrecord",
        "sms": "smsmessage",
        "calls": "calllog",
    }

    coll = collection_map.get(resource)
    if not coll:
        raise HTTPException(status_code=400, detail="Unknown resource")

    docs = get_documents(coll, {}, limit)

    if not docs:
        return ""

    # Prepare CSV
    output = io.StringIO()
    writer = csv.DictWriter(output, fieldnames=sorted({k for d in docs for k in d.keys()}))
    writer.writeheader()
    for d in docs:
        d = {**d}
        if d.get("_id"):
            d["_id"] = str(d["_id"])  # stringify ObjectId
        writer.writerow(d)

    return output.getvalue()

# ------------------------------------------------------
# Twilio: SMS send + webhook; Voice outbound + inbound webhook (TwiML)
# ------------------------------------------------------

class SmsSendRequest(BaseModel):
    to: str
    body: str


@app.post("/sms/send")
async def sms_send(req: SmsSendRequest):
    from_number = os.getenv("TWILIO_PHONE_NUMBER")
    if not from_number:
        raise HTTPException(status_code=500, detail="TWILIO_PHONE_NUMBER not configured")

    client = get_twilio_client()
    try:
        message = client.messages.create(
            body=req.body,
            from_=from_number,
            to=req.to
        )
        create_document("smsmessage", SmsMessage(
            to=req.to,
            from_number=from_number,
            body=req.body,
            direction="outbound",
            status="queued",
            sid=message.sid
        ))
        return {"sid": message.sid, "status": message.status}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/sms")
async def list_sms(limit: int = 200):
    docs = get_documents("smsmessage", {}, limit)
    for d in docs:
        d["_id"] = str(d.get("_id"))
    return docs


@app.post("/sms/webhook", response_class=PlainTextResponse)
async def sms_webhook(request: Request):
    form = await request.form()
    form_dict = dict(form)

    if not validate_twilio_request(request, form_dict):
        raise HTTPException(status_code=403, detail="Invalid Twilio signature")

    from_number = form.get("From")
    to_number = form.get("To")
    body = form.get("Body", "")

    # Save inbound message
    create_document("smsmessage", SmsMessage(
        to=to_number,
        from_number=from_number,
        body=body,
        direction="inbound",
        status="received"
    ))

    # Simple auto-reply
    resp = MessagingResponse()
    resp.message("Thanks for texting AHC! We received: '" + (body or "") + "'. We'll be in touch shortly.")
    return str(resp)


class CallRequest(BaseModel):
    to: str
    # Optional: URL for TwiML to play
    twiml_url: Optional[str] = None


@app.post("/voice/call")
async def voice_call(req: CallRequest):
    from_number = os.getenv("TWILIO_PHONE_NUMBER")
    if not from_number:
        raise HTTPException(status_code=500, detail="TWILIO_PHONE_NUMBER not configured")

    client = get_twilio_client()
    try:
        if req.twiml_url:
            call = client.calls.create(to=req.to, from_=from_number, url=req.twiml_url)
        else:
            # Use our own endpoint to serve dynamic TwiML greeting
            base_url = os.getenv("PUBLIC_BACKEND_URL")
            if not base_url:
                raise HTTPException(status_code=500, detail="PUBLIC_BACKEND_URL not set for voice callback")
            call = client.calls.create(to=req.to, from_=from_number, url=f"{base_url}/voice/twiml")

        create_document("calllog", CallLog(
            to=req.to,
            from_number=from_number,
            sid=call.sid,
            status=call.status,
            direction="outbound"
        ))
        return {"sid": call.sid, "status": call.status}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/calls")
async def list_calls(limit: int = 200):
    docs = get_documents("calllog", {}, limit)
    for d in docs:
        d["_id"] = str(d.get("_id"))
    return docs


@app.post("/voice/twiml", response_class=PlainTextResponse)
async def voice_twiml(request: Request):
    form = await request.form()
    form_dict = dict(form)
    if not validate_twilio_request(request, form_dict):
        raise HTTPException(status_code=403, detail="Invalid Twilio signature")

    # Log inbound call start
    from_number = form.get("From")
    to_number = form.get("To")
    sid = form.get("CallSid")
    try:
        create_document("calllog", CallLog(
            to=to_number,
            from_number=from_number,
            sid=sid,
            status="inbound-start",
            direction="inbound"
        ))
    except Exception:
        pass

    # Simple IVR greeting and gather
    vr = VoiceResponse()
    base_url = os.getenv("PUBLIC_BACKEND_URL", "")
    action_path = "/voice/handle-gather"
    action_url = (base_url.rstrip("/") + action_path) if base_url else action_path

    gather = Gather(action=action_url, num_digits=1, timeout=6)
    gather.say("Welcome to A H C front desk. Press 1 to book a demo. Press 2 for support. Press 3 for sales.")
    vr.append(gather)
    vr.say("We didn't receive any input. Goodbye.")
    return str(vr)


@app.post("/voice/handle-gather", response_class=PlainTextResponse)
async def voice_handle_gather(request: Request):
    form = await request.form()
    form_dict = dict(form)
    if not validate_twilio_request(request, form_dict):
        raise HTTPException(status_code=403, detail="Invalid Twilio signature")

    digits = form.get("Digits")
    from_number = form.get("From")
    to_number = form.get("To")

    vr = VoiceResponse()
    client = None
    try:
        client = get_twilio_client()
    except Exception:
        client = None

    twilio_from = os.getenv("TWILIO_PHONE_NUMBER")

    def safe_sms(to: Optional[str], text: str):
        if client and twilio_from and to:
            try:
                client.messages.create(body=text, from_=twilio_from, to=to)
                create_document("smsmessage", SmsMessage(
                    to=to,
                    from_number=twilio_from,
                    body=text,
                    direction="outbound",
                    status="queued"
                ))
            except Exception:
                pass

    # Actions per IVR selection
    if digits == "1":
        # Book a demo: send scheduling link via SMS and log a lead
        safe_sms(from_number, "Thanks for calling AHC! Schedule your demo here: https://cal.com/ahc/demo")
        try:
            create_document("lead", Lead(
                name="Phone Caller",
                email="caller@unknown.local",
                inquiry_type="demo",
                reason="Selected '1' in IVR; phone: " + (from_number or "unknown")
            ))
        except Exception:
            pass
        vr.say("Great. We'll text you a link to schedule a demo shortly. Goodbye.")
    elif digits == "2":
        # Support: create a ticket and send SMS confirmation
        try:
            create_document("supportticket", SupportTicket(
                name="Phone Caller",
                email="caller@unknown.local",
                issue_type="other",
                subject="Support via IVR",
                description=f"Caller {from_number} requested support via IVR",
                priority="medium"
            ))
        except Exception:
            pass
        safe_sms(from_number, "Support request received. Our team will follow up shortly. Reply here with details.")
        vr.say("Support selected. We will follow up by text. Goodbye.")
    elif digits == "3":
        # Sales: log a lead and text a sales link
        safe_sms(from_number, "Thanks! A member of sales will reach out. Learn more: https://example.com/sales")
        try:
            create_document("lead", Lead(
                name="Phone Caller",
                email="caller@unknown.local",
                inquiry_type="purchase",
                reason="Selected '3' in IVR; sales interest"
            ))
        except Exception:
            pass
        vr.say("Sales selected. Our team will reach out. Goodbye.")
    else:
        vr.say("Invalid selection. Goodbye.")

    return str(vr)


# Health for Twilio webhooks GET (optional)
@app.get("/sms/webhook")
async def sms_webhook_get():
    return {"status": "ok"}


@app.get("/voice/twiml")
async def voice_twiml_get():
    return {"status": "ok"}


if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)
