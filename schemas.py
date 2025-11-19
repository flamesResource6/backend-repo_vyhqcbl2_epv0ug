"""
Database Schemas

Define your MongoDB collection schemas here using Pydantic models.
These schemas are used for data validation in your application.

Each Pydantic model represents a collection in your database.
Model name is converted to lowercase for the collection name:
- User -> "user" collection
- Product -> "product" collection
- BlogPost -> "blogs" collection
"""

from pydantic import BaseModel, Field, EmailStr
from typing import Optional, List, Literal
from datetime import datetime

# ---------------------------------------------------------------------
# Core AHC Front Desk Assistant Schemas
# ---------------------------------------------------------------------

InquiryType = Literal["demo", "purchase", "support", "partnership", "faq", "other"]

class Lead(BaseModel):
    """
    Leads collected from the assistant
    Collection: "lead"
    """
    name: str = Field(..., description="Full name")
    email: EmailStr = Field(..., description="Email address")
    company: Optional[str] = Field(None, description="Company name")
    inquiry_type: InquiryType = Field(..., description="Type of inquiry")
    reason: Optional[str] = Field(None, description="Reason for contact / free text")
    qualification: Optional[str] = Field(None, description="Lead qualification notes or score")

class ChatMessage(BaseModel):
    """
    Individual chat messages captured by the widget
    Collection: "chatmessage"
    """
    session_id: str = Field(..., description="Client-side chat/session identifier")
    sender: Literal["user", "assistant", "system"] = Field(...)
    content: str = Field(..., description="Message text")
    topic: Optional[str] = Field(None, description="Topic tag e.g., pricing, support")

class Booking(BaseModel):
    """
    Product demo booking
    Collection: "booking"
    """
    name: str
    email: EmailStr
    company: Optional[str] = None
    slot_iso: str = Field(..., description="ISO datetime for the demo slot")
    notes: Optional[str] = None
    source: Optional[str] = Field("chat", description="Where booking originated")

class SupportTicket(BaseModel):
    """
    Support/technical issues routed to internal team
    Collection: "supportticket"
    """
    name: str
    email: EmailStr
    company: Optional[str] = None
    issue_type: Literal["technical", "billing", "account", "other"]
    subject: str
    description: str
    priority: Literal["low", "medium", "high"] = "medium"
    tags: List[str] = []

class PaymentRecord(BaseModel):
    """
    Records for payment/upgrade attempts
    Collection: "paymentrecord"
    """
    name: Optional[str] = None
    email: Optional[EmailStr] = None
    plan: str
    amount_cents: int
    currency: str = "usd"
    status: Literal["initiated", "succeeded", "failed"] = "initiated"
    provider: str = "stripe"
    checkout_session_id: Optional[str] = None

# ---------------------------------------------------------------------
# Example schemas (kept for reference)
# ---------------------------------------------------------------------

class User(BaseModel):
    """
    Users collection schema
    Collection name: "user" (lowercase of class name)
    """
    name: str = Field(..., description="Full name")
    email: str = Field(..., description="Email address")
    address: str = Field(..., description="Address")
    age: Optional[int] = Field(None, ge=0, le=120, description="Age in years")
    is_active: bool = Field(True, description="Whether user is active")

class Product(BaseModel):
    """
    Products collection schema
    Collection name: "product" (lowercase of class name)
    """
    title: str = Field(..., description="Product title")
    description: Optional[str] = Field(None, description="Product description")
    price: float = Field(..., ge=0, description="Price in dollars")
    category: str = Field(..., description="Product category")
    in_stock: bool = Field(True, description="Whether product is in stock")
