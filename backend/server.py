from fastapi import FastAPI, APIRouter, HTTPException
from fastapi.responses import HTMLResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.cors import CORSMiddleware
from motor.motor_asyncio import AsyncIOMotorClient
import certifi
import os
import re
import uuid
import string
import random
import logging
from pathlib import Path
from pydantic import BaseModel, Field
from typing import Optional
from datetime import datetime, timezone
from dotenv import load_dotenv

from gemini_client import generate_html

ROOT_DIR = Path(__file__).parent
load_dotenv(ROOT_DIR / ".env")

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("pagecraft")

MONGO_URL = os.environ["MONGO_URL"]
DB_NAME = os.environ["DB_NAME"]

client = AsyncIOMotorClient(MONGO_URL, tlsCAFile=certifi.where())
db = client[DB_NAME]

app = FastAPI(title="PageCraft")
api = APIRouter(prefix="/api")


# ---------- Models ----------
class PageCreate(BaseModel):
    business_name: str
    industry: str
    description: str  # what the business does
    target_audience: Optional[str] = ""
    style_preference: Optional[str] = ""  # e.g. "modern minimal", "bold colorful", "elegant luxury"
    primary_color: Optional[str] = ""
    sections_wanted: Optional[str] = ""  # free text, e.g. "hero, services, testimonials, contact"
    cta_goal: Optional[str] = ""  # e.g. "get a free quote", "book a table", "sign up"
    contact_phone: Optional[str] = ""
    contact_email: Optional[str] = ""
    contact_social: Optional[str] = ""
    extra_notes: Optional[str] = ""


class DomainUpdate(BaseModel):
    domain: str  # e.g. "cafearoma.com" — no protocol, no trailing slash


class HtmlUpdate(BaseModel):
    html: str  # full replacement HTML, from manual code editing


class EditInstruction(BaseModel):
    instruction: str  # natural-language change request, e.g. "make the CTA button red"


class RenameUpdate(BaseModel):
    business_name: str


# ---------- Prompt ----------
SYSTEM_INSTRUCTION = """You are an elite landing page designer and frontend developer, the kind hired
specifically because your work never looks templated or AI-generated. You write complete, single-file,
production-quality HTML pages with inline CSS in a <style> tag — no external CSS/JS files, no build step,
no frameworks. The page must:
- Be grounded in the SPECIFIC business given — its industry, audience, and personality should visibly shape
  the color palette, typography, and copy. A cafe and a law firm should never look like they came from the
  same template.
- Avoid default AI-generated design patterns: don't reach for warm cream backgrounds with terracotta accents,
  near-black backgrounds with a single neon accent, or generic purple-gradient SaaS-template looks, unless the
  business's own brief genuinely calls for one of those. Make a deliberate palette choice for THIS business.
- Be fully responsive (mobile, tablet, desktop) using modern CSS (flexbox/grid, clamp() for fluid type).
- Use real typography choices via Google Fonts <link> — pair a characterful display face with a clean body
  face, chosen for this business's personality, not the same pairing every time.
- Do NOT link to any external or stock photo URLs (no placeholder image services, no unsplash/pexels links —
  these break once shared). Instead, include 2-4 designated IMAGE SLOTS the client can fill in later:
  a hero image slot, and 1-3 more where photos would naturally belong (e.g. a gallery, a team/founder photo,
  a product/food shot). Each slot must be a <div> with class="pc-slot" and a data-slot-label attribute
  describing it (e.g. data-slot-label="Hero photo"), sized appropriately for its position (e.g. full-width
  and tall for a hero slot, square or portrait for a smaller one), and styled as an elegant, finished-looking
  placeholder using CSS gradients matching the page's palette, plus a centered inline SVG camera/image icon
  at low opacity — so the page looks complete and polished even before real photos are added. Example:
  <div class="pc-slot" data-slot-label="Hero photo" style="width:100%;height:420px;border-radius:12px;
  background:linear-gradient(135deg, COLOR1, COLOR2);display:flex;align-items:center;justify-content:center;">
  <svg>...camera icon...</svg></div>
- Include a clear hero section with a headline, subheadline, and a prominent call-to-action button.
- Include the specific sections requested, in a logical order, each meaningfully written for THIS specific
  business — not generic filler text.
- Wrap EVERY major section (hero, about, services, testimonials, gallery, contact, etc.) in its own
  <section class="pc-section" data-section-name="Short Label"> ... </section> — this is required, even for
  the hero and footer. This lets the page be edited section-by-section later, so get it right every time.
- Include a real contact/footer section using the exact contact details given (also wrapped as a pc-section).
- Include a sticky/simple nav if there are multiple sections, with working #anchor links.
- Take one deliberate, justified design risk that makes this page memorable — but keep everything else quiet
  and disciplined around it.
- Return ONLY the raw HTML, starting with <!DOCTYPE html> and ending with </html>. No markdown code
  fences, no explanation, no commentary before or after."""


def page_prompt(p: PageCreate) -> str:
    return f"""Build a complete landing page for this business.

Business name: {p.business_name}
Industry: {p.industry}
What they do: {p.description}
Target audience: {p.target_audience or "General local customers"}
Style preference: {p.style_preference or "Modern and clean, your best judgment for this industry"}
Primary color preference: {p.primary_color or "Your best judgment for this industry"}
Sections wanted: {p.sections_wanted or "Hero, About, Services/Offerings, Why Choose Us, Contact"}
Main call-to-action goal: {p.cta_goal or "Get in touch"}
Contact phone: {p.contact_phone or "N/A"}
Contact email: {p.contact_email or "N/A"}
Contact / social link: {p.contact_social or "N/A"}
Additional notes: {p.extra_notes or "N/A"}

Write real, specific copy for this business — not lorem ipsum, not generic placeholders. Make it sound
like it was written by a copywriter who understands this specific industry and audience."""


EDIT_SYSTEM_INSTRUCTION = """You are an elite frontend developer editing an existing single-file HTML page
(inline CSS, no external files, no frameworks). You will be given the current full HTML and a plain-language
change request. Apply ONLY the requested change — do not rewrite unrelated sections, do not change copy,
layout, or colors that weren't asked about, and do not "improve" things that weren't mentioned. Preserve
everything else exactly as it was, including all class="pc-section" wrappers and class="pc-slot" image
placeholders exactly as they are (do not remove, rename, or restructure them unless the request specifically
asks to add/remove a section or image). Return ONLY the complete, updated raw HTML, starting with
<!DOCTYPE html> and ending with </html>. No markdown code fences, no explanation, no commentary before or after."""


def edit_prompt(current_html: str, instruction: str) -> str:
    return f"""CURRENT PAGE HTML:
{current_html}

REQUESTED CHANGE:
{instruction}

Apply exactly this change to the page above and return the complete updated HTML."""


def extract_html(text: str) -> str:
    text = text.strip()
    # strip markdown code fences if the model added them anyway
    text = re.sub(r"^```(?:html)?\s*", "", text)
    text = re.sub(r"\s*```$", "", text)
    return text.strip()


def make_slug(business_name: str) -> str:
    base = re.sub(r"[^a-z0-9]+", "-", business_name.lower()).strip("-")[:40] or "page"
    suffix = "".join(random.choices(string.ascii_lowercase + string.digits, k=6))
    return f"{base}-{suffix}"


# ---------- Routes ----------
@api.post("/generate")
async def generate_page(body: PageCreate):
    try:
        raw = generate_html(page_prompt(body), SYSTEM_INSTRUCTION)
        html = extract_html(raw)
        if "<html" not in html.lower():
            raise ValueError("Model did not return a valid HTML document")
    except Exception as e:
        logger.exception("Page generation failed")
        raise HTTPException(status_code=500, detail=f"AI generation failed: {str(e)}")

    slug = make_slug(body.business_name)
    now = datetime.now(timezone.utc).isoformat()
    doc = {
        "id": str(uuid.uuid4()),
        "slug": slug,
        "business_name": body.business_name,
        "industry": body.industry,
        "html": html,
        "created_at": now,
    }
    await db.pages.insert_one(doc)
    return {"slug": slug, "business_name": body.business_name, "html": html, "created_at": now}


@api.get("/pages")
async def list_pages():
    pages = await db.pages.find({}, {"_id": 0, "html": 0}).sort("created_at", -1).to_list(200)
    return pages


@api.get("/pages/{slug}")
async def get_page_meta(slug: str):
    page = await db.pages.find_one({"slug": slug}, {"_id": 0})
    if not page:
        raise HTTPException(status_code=404, detail="Page not found")
    return page


@api.patch("/pages/{slug}/rename")
async def rename_page(slug: str, body: RenameUpdate):
    result = await db.pages.update_one(
        {"slug": slug}, {"$set": {"business_name": body.business_name}}
    )
    if result.matched_count == 0:
        raise HTTPException(status_code=404, detail="Page not found")
    return {"success": True}


@api.delete("/pages/{slug}")
async def delete_page(slug: str):
    result = await db.pages.delete_one({"slug": slug})
    if result.deleted_count == 0:
        raise HTTPException(status_code=404, detail="Page not found")
    return {"success": True}


@api.put("/pages/{slug}")
async def update_page_html(slug: str, body: HtmlUpdate):
    """Save a manually-edited version of the page's HTML directly."""
    html = extract_html(body.html)
    if "<html" not in html.lower():
        raise HTTPException(status_code=400, detail="That doesn't look like a valid HTML document")
    result = await db.pages.update_one(
        {"slug": slug},
        {"$set": {"html": html, "updated_at": datetime.now(timezone.utc).isoformat()}}
    )
    if result.matched_count == 0:
        raise HTTPException(status_code=404, detail="Page not found")
    return {"success": True, "html": html}


@api.post("/pages/{slug}/edit")
async def ai_edit_page(slug: str, body: EditInstruction):
    """Ask the AI to apply a specific, described change to the existing page."""
    page = await db.pages.find_one({"slug": slug}, {"_id": 0})
    if not page:
        raise HTTPException(status_code=404, detail="Page not found")
    try:
        raw = generate_html(edit_prompt(page["html"], body.instruction), EDIT_SYSTEM_INSTRUCTION)
        html = extract_html(raw)
        if "<html" not in html.lower():
            raise ValueError("Model did not return a valid HTML document")
    except Exception as e:
        logger.exception("AI edit failed")
        raise HTTPException(status_code=500, detail=f"AI edit failed: {str(e)}")

    await db.pages.update_one(
        {"slug": slug},
        {"$set": {"html": html, "updated_at": datetime.now(timezone.utc).isoformat()}}
    )
    return {"success": True, "html": html}


@api.put("/pages/{slug}/domain")
async def set_page_domain(slug: str, body: DomainUpdate):
    domain = body.domain.strip().lower()
    domain = re.sub(r"^https?://", "", domain).rstrip("/")
    if not domain:
        raise HTTPException(status_code=400, detail="Domain cannot be empty")
    existing = await db.pages.find_one({"domain": domain})
    if existing and existing.get("slug") != slug:
        raise HTTPException(status_code=409, detail="This domain is already attached to another page")
    result = await db.pages.update_one({"slug": slug}, {"$set": {"domain": domain}})
    if result.matched_count == 0:
        raise HTTPException(status_code=404, detail="Page not found")
    return {"success": True, "domain": domain}


@api.delete("/pages/{slug}/domain")
async def remove_page_domain(slug: str):
    result = await db.pages.update_one({"slug": slug}, {"$unset": {"domain": ""}})
    if result.matched_count == 0:
        raise HTTPException(status_code=404, detail="Page not found")
    return {"success": True}


app.include_router(api)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ---------- Serve the generated page at the shareable link ----------
@app.get("/p/{slug}", response_class=HTMLResponse)
async def serve_page(slug: str):
    page = await db.pages.find_one({"slug": slug}, {"_id": 0})
    if not page:
        return HTMLResponse("<h1>Page not found</h1>", status_code=404)
    return HTMLResponse(page["html"])


# ---------- Serve pages on their attached custom domain, at the root ----------
@app.middleware("http")
async def custom_domain_router(request, call_next):
    host = request.headers.get("host", "").split(":")[0].lower()
    known_hosts = {
        "localhost", "127.0.0.1",
        os.environ.get("RENDER_EXTERNAL_HOSTNAME", "").lower(),
    }
    if request.url.path == "/" and host and host not in known_hosts and not host.endswith(".onrender.com"):
        page = await db.pages.find_one({"domain": host}, {"_id": 0})
        if page:
            return HTMLResponse(page["html"])
    return await call_next(request)


# ---------- Serve the simple form UI ----------
static_dir = ROOT_DIR / "static"
if static_dir.exists():
    app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

    @app.get("/")
    async def serve_form():
        return FileResponse(str(static_dir / "index.html"))
