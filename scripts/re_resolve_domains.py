"""
re_resolve_domains.py — Bulk domain re-resolution for all startups in the DB.

Scans every startup's current domain for validity (parked shells, wrong companies,
timeouts, redirects to unrelated sites). For any domain that fails the check,
re-runs the domain resolver (Stage 2 DNS guessing → Stage 3 Claude AI search)
to find the correct domain.

Usage:
    # Preview what would change (no writes)
    python3 scripts/re_resolve_domains.py --dry-run

    # Apply all fixes
    python3 scripts/re_resolve_domains.py --apply

    # Target specific companies only
    python3 scripts/re_resolve_domains.py --apply --companies "Axelera,Slang Ai"
"""

import argparse
import os
import sys
import time
import warnings
from urllib.parse import urlparse

import requests
warnings.filterwarnings('ignore')

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
load_dotenv(override=True)

from supabase import create_client
from app.resolution.domain_resolver import DomainResolver
from app.core.database import DatabaseClient


# ---------------------------------------------------------------------------
# Validity check — mirrors domain_resolver._name_appears_on_homepage() logic
# ---------------------------------------------------------------------------

# Known parking / redirect-shell patterns in page content
PARKING_SNIPPETS = [
    'window.location.href="/lander"',   # The common parked template we've seen
    'window.location.replace(',          # JS redirect shells
    'sedo.com', 'godaddy.com/domain',   # Domain brokers
    'this domain is for sale',
    'domain is registered',
    'buy this domain',
]

# Known bad redirect destination patterns (redirect target host contains these)
BAD_REDIRECT_PATTERNS = [
    'sedo.', 'dan.com', 'afternic.', 'godaddy.', 'namecheap.',
    'hoojacont.', 'imagegenerators.',   # Seen in our dataset
    'twitch.tv',                         # matx.io → twitch
    '.ooe.gv.at',                        # frankenburg → Austrian municipality
]


def _check_domain_validity(company_name: str, domain: str) -> tuple[bool, str]:
    """
    Returns (is_valid, reason).
    is_valid=False means the domain needs re-resolution.
    """
    company_slug = company_name.lower().replace(' ai', '').replace(' ', '').replace('ai', '')
    # Keep at least 4 chars for meaningful matching
    if len(company_slug) < 4:
        company_slug = company_name.lower().replace(' ', '')

    try:
        resp = requests.get(
            f'https://{domain}',
            timeout=7,
            headers={'User-Agent': 'Mozilla/5.0 (compatible; GenAI-Intel/1.0)'},
            verify=False,
            allow_redirects=True,
        )

        final_url = resp.url
        final_host = urlparse(final_url).netloc.lower().replace('www.', '')
        content = resp.text
        content_lower = content.lower()

        # 1. Redirect to a known bad destination
        for pat in BAD_REDIRECT_PATTERNS:
            if pat in final_host:
                return False, f'redirects to bad host: {final_host}'

        # 2. Redirected to a completely unrelated domain (neither original domain
        #    nor company slug present in the final host)
        if domain not in final_host and company_slug[:5] not in final_host:
            return False, f'redirected to unrelated host: {final_host}'

        # 3. HTTP error codes
        if resp.status_code == 404:
            return False, f'HTTP 404'
        if resp.status_code >= 500:
            return False, f'HTTP {resp.status_code}'

        # 4. Parked / redirect shell (tiny page with JS redirect)
        if len(content.strip()) < 300:
            return False, f'near-empty response ({len(content)} chars)'

        # 5. Known parking page snippets
        for snippet in PARKING_SNIPPETS:
            if snippet.lower() in content_lower:
                return False, f'parking/redirect shell detected'

        # 6. Company name not in page content at all
        # (Use first 5 chars of slug as a loose check to handle variations)
        if company_slug[:5] not in content_lower and company_name.lower() not in content_lower:
            # Don't fail on this alone — 403s, JS SPAs, and different trading names
            # are common. Just flag it as a soft warning, not a hard failure.
            return True, f'OK (name not in page — may be SPA/403)'

        return True, 'OK'

    except requests.exceptions.Timeout:
        return False, 'timeout'
    except requests.exceptions.ConnectionError as e:
        return False, f'connection error: {str(e)[:60]}'
    except Exception as e:
        return False, f'error: {str(e)[:60]}'


# ---------------------------------------------------------------------------
# Main script
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description='Re-resolve startup domains')
    parser.add_argument('--dry-run', action='store_true', default=False,
                        help='Show what would change without writing (default)')
    parser.add_argument('--apply', action='store_true', default=False,
                        help='Write changes to the database')
    parser.add_argument('--companies', type=str, default=None,
                        help='Comma-separated list of company names to process (default: all)')
    args = parser.parse_args()

    if not args.apply:
        args.dry_run = True  # default to dry-run if neither flag given

    mode = 'DRY RUN' if args.dry_run else 'APPLY'
    print(f'\n{"="*60}')
    print(f'  Domain Re-Resolution — {mode}')
    print(f'{"="*60}\n')

    sb = create_client(os.getenv('SUPABASE_URL'), os.getenv('SUPABASE_KEY'))
    db = DatabaseClient()
    resolver = DomainResolver()

    # Fetch all startups
    result = sb.table('startups').select(
        'id, canonical_name, website, description, lead_investors'
    ).order('canonical_name').execute()

    all_startups = result.data
    print(f'Loaded {len(all_startups)} startups from DB\n')

    # Filter to specific companies if requested
    if args.companies:
        targets = {c.strip().lower() for c in args.companies.split(',')}
        all_startups = [
            s for s in all_startups
            if s['canonical_name'].lower() in targets
        ]
        print(f'Filtered to {len(all_startups)} company/companies: {args.companies}\n')

    # ---------------------------------------------------------------------------
    # Phase 1: Validity scan
    # ---------------------------------------------------------------------------
    print('Phase 1: Scanning all domains for validity...\n')

    needs_resolution = []   # (startup_row, reason)
    already_valid    = []   # (startup_row, reason)

    for startup in all_startups:
        company = startup['canonical_name']
        domain  = startup['website']
        is_valid, reason = _check_domain_validity(company, domain)

        if is_valid:
            already_valid.append((startup, reason))
            print(f'  ✅ {company} ({domain}) — {reason}')
        else:
            needs_resolution.append((startup, reason))
            print(f'  ⚠️  {company} ({domain}) — {reason}')

    print(f'\n  Valid: {len(already_valid)}  |  Needs re-resolution: {len(needs_resolution)}\n')

    if not needs_resolution:
        print('Nothing to fix. All domains look valid.')
        return

    # ---------------------------------------------------------------------------
    # Phase 2: Re-resolve flagged companies
    # ---------------------------------------------------------------------------
    print(f'{"="*60}')
    print(f'Phase 2: Re-resolving {len(needs_resolution)} flagged domain(s)...\n')

    resolved_count   = 0
    unresolved_count = 0
    changed_count    = 0
    results_log      = []  # For final summary

    for startup, flag_reason in needs_resolution:
        company    = startup['canonical_name']
        old_domain = startup['website']
        startup_id = startup['id']
        description    = startup.get('description') or None
        lead_investors = startup.get('lead_investors') or []

        print(f'  → {company} (current: {old_domain})')
        print(f'    Flagged: {flag_reason}')

        new_domain = resolver.resolve(
            company_name=company,
            description=description,
            lead_investors=lead_investors if lead_investors else None,
        )

        if not new_domain:
            print(f'    ❌ Could not resolve — leaving unchanged\n')
            unresolved_count += 1
            results_log.append((company, old_domain, None, flag_reason, 'UNRESOLVED'))
            time.sleep(1)
            continue

        if new_domain == old_domain:
            print(f'    ℹ️  Resolver returned same domain ({new_domain}) — no change\n')
            resolved_count += 1
            results_log.append((company, old_domain, new_domain, flag_reason, 'SAME'))
            time.sleep(1)
            continue

        print(f'    ✅ New domain: {new_domain}')

        if args.dry_run:
            print(f'    [DRY RUN] Would update: {old_domain} → {new_domain}\n')
            results_log.append((company, old_domain, new_domain, flag_reason, 'WOULD_UPDATE'))
            changed_count += 1
        else:
            # Write changes
            try:
                # 1. Delete stale attribution signals
                db.delete_signals_for_startup(startup_id)
                print(f'    🗑  Deleted attribution signals')

                # 2. Delete stale attribution snapshots
                sb.table('attribution_snapshots').delete().eq('startup_id', startup_id).execute()
                print(f'    🗑  Deleted attribution snapshots')

                # 3. Update domain in startups table
                sb.table('startups').update({'website': new_domain}).eq('id', startup_id).execute()
                print(f'    💾 Updated domain: {old_domain} → {new_domain}\n')

                results_log.append((company, old_domain, new_domain, flag_reason, 'UPDATED'))
                changed_count += 1

            except Exception as e:
                print(f'    ⚠️  DB write failed: {e}\n')
                results_log.append((company, old_domain, new_domain, flag_reason, f'ERROR: {e}'))

        time.sleep(5)  # Rate limit — Claude Sonnet web search uses ~3k tokens/call

    # ---------------------------------------------------------------------------
    # Summary
    # ---------------------------------------------------------------------------
    print(f'\n{"="*60}')
    print(f'Summary ({mode})')
    print(f'{"="*60}')
    print(f'  Total scanned:      {len(all_startups)}')
    print(f'  Already valid:      {len(already_valid)}')
    print(f'  Flagged:            {len(needs_resolution)}')
    print(f'  {"Would update" if args.dry_run else "Updated"}:       {changed_count}')
    print(f'  Same domain:        {resolved_count}')
    print(f'  Could not resolve:  {unresolved_count}')

    if results_log:
        print(f'\nChanges:')
        for company, old, new, reason, status in results_log:
            if new and new != old:
                print(f'  {company}: {old} → {new}  [{status}]')
            elif status == 'UNRESOLVED':
                print(f'  {company}: {old} → ? (unresolved)')

    if args.dry_run:
        print(f'\n  ℹ️  Dry run — no changes written. Run with --apply to update the DB.')
    else:
        print(f'\n  ✅ Done. Attribution will be refreshed on the next cron run.')
    print()


if __name__ == '__main__':
    main()
