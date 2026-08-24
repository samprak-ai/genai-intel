"""
clear_db.py — wipe all tables in the genai-intel Supabase database.
Uses the Supabase REST API via the anon key (delete via .neq filter trick).
Run: python3 clear_db.py
"""
import os
from dotenv import load_dotenv
from supabase import create_client

load_dotenv()

SUPABASE_URL = os.environ["SUPABASE_URL"]
SUPABASE_KEY = os.environ["SUPABASE_KEY"]

client = create_client(SUPABASE_URL, SUPABASE_KEY)

# Order matters — delete child tables before parents to avoid FK violations
TABLES = [
    "pipeline_logs",
    "manual_overrides",
    "weekly_runs",
    "attribution_snapshots",
    "attribution_signals",
    "funding_events",
    "startups",
]

print("🗑️  Clearing database...")
for table in TABLES:
    try:
        # .neq on id with an impossible value matches all rows
        result = client.table(table).delete().neq("id", "00000000-0000-0000-0000-000000000000").execute()
        print(f"  ✅ {table} — cleared")
    except Exception as e:
        print(f"  ⚠️  {table} — {e}")

print("\n✅ Done. Database is empty.")
