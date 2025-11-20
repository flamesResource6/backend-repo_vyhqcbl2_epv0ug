import os
from typing import List, Optional
from fastapi import FastAPI, HTTPException, Request, Form
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel

from database import db, create_document, get_documents
from schemas import Lead, ChatMessage, Booking, SupportTicket, PaymentRecord, SmsMessage, CallLog

# Twilio SDK
from twilio.rest import Client as TwilioClient
from twilio.twiml.messaging_response import MessagingResponse
from twilio.twiml.voice_response import VoiceResponse, Gather
from twilio.request_validator import RequestValidator

app = FastAPI(title="AHC Front Desk Assistant API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


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

    return response

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
