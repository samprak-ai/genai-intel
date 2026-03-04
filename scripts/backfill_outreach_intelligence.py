"""
Backfill outreach intelligence for all Tier 1+2 companies.

Generates engagement timing + recommended angle via Claude Haiku for every
Tier 1 and Tier 2 company that doesn't have outreach intelligence yet.

Usage:
    python3 scripts/backfill_outreach_intelligence.py --dry-run     # preview only
    python3 scripts/backfill_outreach_intelligence.py               # run for real
"""

import os
import sys
import argparse

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from dotenv import load_dotenv
load_dotenv(override=True)

from app.intelligence.outreach_generator import run_outreach_generation


def main():
    parser = argparse.ArgumentParser(description='Backfill outreach intelligence for Tier 1+2 companies')
    parser.add_argument('--dry-run', action='store_true', help='Calculate timing but do not call LLM or write to DB')
    args = parser.parse_args()

    print("\n🧠 Backfilling outreach intelligence...")
    if args.dry_run:
        print("   (DRY RUN — no LLM calls or DB writes)\n")

    result = run_outreach_generation(dry_run=args.dry_run)

    if result['companies_processed'] == 0:
        print("\n⚠️  No Tier 1/2 companies found. Did you run the SQL migration and engagement tier backfill first?")
    else:
        print(f"\n✅ Done.")


if __name__ == '__main__':
    main()
