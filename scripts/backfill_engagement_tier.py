"""
Backfill engagement tiers for all existing companies.

Usage:
    python scripts/backfill_engagement_tier.py --dry-run     # preview only
    python scripts/backfill_engagement_tier.py               # run for real
"""

import os
import sys
import argparse

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from dotenv import load_dotenv
load_dotenv(override=True)

from app.priority import recalculate_all_priorities


def main():
    parser = argparse.ArgumentParser(description='Backfill engagement tiers for all companies')
    parser.add_argument('--dry-run', action='store_true', help='Calculate tiers but do not write to DB')
    args = parser.parse_args()

    print("\n🔄 Backfilling engagement tiers...")
    if args.dry_run:
        print("   (DRY RUN — no DB writes)\n")

    result = recalculate_all_priorities(dry_run=args.dry_run)

    if result['total'] == 0:
        print("\n⚠️  No companies found. Did you run the SQL migration first?")
    else:
        print(f"\n✅ Done.")


if __name__ == '__main__':
    main()
