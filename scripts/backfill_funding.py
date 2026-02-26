"""
One-shot script to backfill funding_events for the 22 manually-seeded startups.

Data sourced from the Feb-2026 funding tracker spreadsheet.
Safe to re-run — uses upsert on (startup_id, funding_amount_usd, announcement_date).

Usage:
    python scripts/backfill_funding.py
"""

import os
import sys
from datetime import date

# Allow imports from project root
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
load_dotenv()

from app.core.database import DatabaseClient
from app.models import FundingEvent, Startup

# ---------------------------------------------------------------------------
# Funding data from the Feb-2026 tracker spreadsheet
# Dates: "Feb-26" → 2026-02-01, "Jan-26" → 2026-01-01
# Amounts: in MILLIONS USD as required by FundingEvent model
#   ($30B → 30_000, $1B → 1_000, $520M → 520, etc.)
# ---------------------------------------------------------------------------
FUNDING_DATA = [
    {
        "company_name":  "Anthropic",
        "website":       "anthropic.com",
        "amount":        30_000,       # $30B
        "stage":         "Series G",
        "date":          date(2026, 2, 1),
        "industry":      "Generative AI",
        "investors":     ["GIC", "Coatue", "Nvidia"],
    },
    {
        "company_name":  "World Labs",
        "website":       "worldlabs.ai",
        "amount":        1_000,        # $1B
        "stage":         "Undisclosed",
        "date":          date(2026, 2, 1),
        "industry":      "Spatial Intelligence",
        "investors":     ["Autodesk"],
    },
    {
        "company_name":  "Apptronik",
        "website":       "apptronik.com",
        "amount":        520,          # $520M
        "stage":         "Series A Ext.",
        "date":          date(2026, 2, 1),
        "industry":      "Humanoid Robotics",
        "investors":     [],
    },
    {
        "company_name":  "ElevenLabs",
        "website":       "elevenlabs.io",
        "amount":        500,          # $500M
        "stage":         "Series D",
        "date":          date(2026, 2, 1),
        "industry":      "Voice AI",
        "investors":     ["Sequoia", "a16z", "Iconiq"],
    },
    {
        "company_name":  "Humans and AI",
        "website":       "humansand.ai",
        "amount":        480,          # $480M
        "stage":         "Seed",
        "date":          date(2026, 1, 1),
        "industry":      "Human-centric AI",
        "investors":     ["Nvidia", "Bezos", "GV"],
    },
    {
        "company_name":  "Inertia",
        "website":       "inertia.com",
        "amount":        450,          # $450M
        "stage":         "Series A",
        "date":          date(2026, 2, 1),
        "industry":      "Fusion Energy",
        "investors":     ["Bessemer", "GV", "Threshold"],
    },
    {
        "company_name":  "Axiom Space",
        "website":       "axiomspace.com",
        "amount":        350,          # $350M
        "stage":         "Undisclosed",
        "date":          date(2026, 1, 1),
        "industry":      "Space Infrastructure",
        "investors":     [],
    },
    {
        "company_name":  "Runway",
        "website":       "runwayml.com",
        "amount":        315,          # $315M
        "stage":         "Series E",
        "date":          date(2026, 2, 1),
        "industry":      "AI Video Generation",
        "investors":     [],
    },
    {
        "company_name":  "Ricursive AI",
        "website":       "ricursive.ai",
        "amount":        300,          # $300M
        "stage":         "Series A",
        "date":          date(2026, 1, 1),
        "industry":      "AI Chip Design",
        "investors":     ["NVentures", "Founders Fund"],
    },
    {
        "company_name":  "Cellares",
        "website":       "cellares.com",
        "amount":        257,          # $257M
        "stage":         "Series D",
        "date":          date(2026, 2, 1),
        "industry":      "Biotech Mfg",
        "investors":     ["BlackRock", "Eclipse"],
    },
    {
        "company_name":  "Fundamental AI",
        "website":       "fundamental.ai",
        "amount":        255,          # $255M
        "stage":         "Series A",
        "date":          date(2026, 2, 1),
        "industry":      "Big Data Analysis",
        "investors":     ["Sequoia", "Greenoaks"],
    },
    {
        "company_name":  "Upwind",
        "website":       "upwind.io",
        "amount":        250,          # $250M
        "stage":         "Series B",
        "date":          date(2026, 1, 1),
        "industry":      "Cloud Security",
        "investors":     ["Bessemer Venture Partners"],
    },
    {
        "company_name":  "Decagon AI",
        "website":       "decagon.ai",
        "amount":        250,          # $250M
        "stage":         "Undisclosed",
        "date":          date(2026, 2, 1),
        "industry":      "Agentic AI",
        "investors":     ["Coatue", "Index Ventures"],
    },
    {
        "company_name":  "Flapping Airplanes",
        "website":       "flappingairplanes.com",
        "amount":        180,          # $180M
        "stage":         "Seed",
        "date":          date(2026, 1, 1),
        "industry":      "Efficient AI",
        "investors":     ["GV", "Sequoia", "a16z"],
    },
    {
        "company_name":  "Goodfire AI",
        "website":       "goodfire.ai",
        "amount":        150,          # $150M
        "stage":         "Series A",
        "date":          date(2026, 2, 1),
        "industry":      "AI Interpretability",
        "investors":     ["Lightspeed", "Benchmark"],
    },
    {
        "company_name":  "Pale Blue Dot",
        "website":       "palebluedot.ai",
        "amount":        150,          # $150M
        "stage":         "Series B",
        "date":          date(2026, 2, 1),
        "industry":      "AI Infrastructure",
        "investors":     ["B Capital Group"],
    },
    {
        "company_name":  "Standard Nuclear",
        "website":       "standardnuclear.com",
        "amount":        140,          # $140M
        "stage":         "Series A",
        "date":          date(2026, 1, 1),
        "industry":      "Nuclear Fuel",
        "investors":     ["Decisive Point"],
    },
    {
        "company_name":  "Resolve AI",
        "website":       "resolve.ai",
        "amount":        125,          # $125M
        "stage":         "Series A",
        "date":          date(2026, 2, 1),
        "industry":      "Autonomous SRE",
        "investors":     ["Lightspeed Venture Partners"],
    },
    {
        "company_name":  "Simile AI",
        "website":       "simile.ai",
        "amount":        100,          # $100M
        "stage":         "Series A",
        "date":          date(2026, 2, 1),
        "industry":      "AI Simulation",
        "investors":     ["Index Ventures"],
    },
    {
        "company_name":  "Loyal",
        "website":       "loyal.com",
        "amount":        100,          # $100M
        "stage":         "Series C",
        "date":          date(2026, 2, 1),
        "industry":      "Biotech (Longevity)",
        "investors":     ["Age1"],
    },
    {
        "company_name":  "Northwood Space",
        "website":       "northwoodspace.io",
        "amount":        100,          # $100M
        "stage":         "Series B",
        "date":          date(2026, 1, 1),
        "industry":      "Space Infrastructure",
        "investors":     ["Washington Harbour", "a16z"],
    },
    {
        "company_name":  "Render",
        "website":       "render.com",
        "amount":        100,          # $100M
        "stage":         "Series C",
        "date":          date(2026, 2, 1),
        "industry":      "AI Cloud Infra",
        "investors":     ["General Catalyst", "Bessemer"],
    },
]


def main():
    db = DatabaseClient()
    ok = 0
    fail = 0

    for row in FUNDING_DATA:
        company = row["company_name"]
        website = row["website"]
        try:
            # Try to find an existing startup by website first.
            # Some entries may have been seeded under a different domain variant
            # (e.g. .com vs .ai) — fall back to a name search in that case.
            existing = db.get_startup_by_website(website)
            if not existing:
                # Try the other common TLD variant if the canonical website differs
                alt_website = website.replace(".ai", ".com") if website.endswith(".ai") else website.replace(".com", ".ai")
                existing = db.get_startup_by_website(alt_website)

            if existing:
                startup = existing
                startup_id = startup["id"]
            else:
                # Create new startup
                startup = db.create_startup(Startup(
                    canonical_name=company,
                    website=website,
                    industry=row["industry"],
                ))
                startup_id = startup["id"]

            # Patch industry separately — create_startup doesn't update existing rows'
            # industry field if the row already exists (upsert only sets on insert).
            # Direct update ensures the value is always written.
            db.client.table("startups").update(
                {"industry": row["industry"]}
            ).eq("id", startup_id).execute()

            # Insert / upsert funding event
            event = FundingEvent(
                company_name=company,
                website=website,
                funding_amount_usd=row["amount"],
                funding_round=row["stage"],
                announcement_date=row["date"],
                lead_investors=row["investors"],
                source_name="manual",
                source_url="manual",
            )
            db.create_funding_event(startup_id, event)

            amt = row['amount']
            amt_display = f"${amt // 1000}B" if amt >= 1000 else f"${amt}M"
            print(f"  ✅ {company} — {amt_display} | {row['stage']} | {row['date']}")
            ok += 1

        except Exception as e:
            print(f"  ❌ {company} — {e}")
            fail += 1

    print(f"\n{'='*50}")
    print(f"Done: {ok} succeeded, {fail} failed")


if __name__ == "__main__":
    main()
