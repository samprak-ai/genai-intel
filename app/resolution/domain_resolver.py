"""
Domain Resolution Module
Reliably finds official company websites using multi-stage cascade
"""

import re
import dns.resolver
import requests
from typing import Optional
from urllib.parse import urlparse
import anthropic
import os


class DomainResolver:
    """
    Resolves company websites using deterministic → heuristic → AI cascade
    
    Philosophy: Try facts first, AI last
    """
    
    def __init__(self):
        self.anthropic_client = anthropic.Anthropic(
            api_key=os.getenv('ANTHROPIC_API_KEY')
        )
        
        # Domains to reject
        self.reject_patterns = [
            'linkedin.com',
            'twitter.com',
            'x.com',
            'facebook.com',
            'instagram.com',
            'crunchbase.com',
            'pitchbook.com',
            'medium.com',
            'substack.com',
            'youtube.com',
            # Domain parking / broker sites (Issue 2)
            'hugedomains.com',
            'sedo.com',
            'dan.com',
            'afternic.com',
            'godaddy.com',
            'namecheap.com',
            'undeveloped.com',
            'brandbucket.com',
            'squadhelp.com',
        ]
    
    def resolve(self, company_name: str, article_text: Optional[str] = None) -> Optional[str]:
        """
        Main resolution method - tries multiple strategies in order
        
        Returns: Clean domain like "company.com" or None
        """
        print(f"\n🔍 Resolving domain for: {company_name}")
        
        # Stage 1: Extract from article (deterministic)
        if article_text:
            domain = self._extract_from_text(article_text)
            if domain:
                print(f"  ✅ Found in article: {domain}")
                return domain
        
        # Stage 2: DNS-based guessing (deterministic)
        domain = self._dns_guessing(company_name)
        if domain:
            print(f"  ✅ Found via DNS: {domain}")
            return domain
        
        # Stage 3: AI web search (last resort)
        domain = self._ai_search(company_name)
        if domain:
            print(f"  ✅ Found via AI search: {domain}")
            return domain
        
        print(f"  ❌ Could not resolve domain")
        return None
    
    def _extract_from_text(self, text: str) -> Optional[str]:
        """
        Stage 1: Extract domain from article text using regex
        
        Looks for patterns like:
        - "visit company.com"
        - "https://company.com"
        - "website: company.com"
        """
        # Pattern 1: Full URLs
        url_pattern = r'https?://([a-z0-9-]+\.[a-z]{2,})'
        matches = re.findall(url_pattern, text.lower())
        
        for match in matches:
            if self._is_valid_domain(match):
                return match
        
        # Pattern 2: Domain-like strings
        domain_pattern = r'\b([a-z0-9-]+\.(?:com|ai|io|net|org|co|tech|app))\b'
        matches = re.findall(domain_pattern, text.lower())
        
        for match in matches:
            if self._is_valid_domain(match):
                return match
        
        return None
    
    def _dns_guessing(self, company_name: str) -> Optional[str]:
        """
        Stage 2: Generate domain candidates and test with DNS
        
        Strategy:
        1. Clean company name (remove Inc, LLC, etc.)
        2. Generate variations (company.com, company.ai, etc.)
        3. Test each with DNS lookup
        4. Return first that resolves
        """
        # Clean company name
        name = company_name.lower()
        name = re.sub(r'\s+(inc\.?|llc|ltd\.?|corporation|corp\.?)$', '', name, flags=re.IGNORECASE)
        name = name.strip()
        
        # Generate candidate domains
        candidates = []
        
        # Variation 1: Remove all spaces
        clean_name = name.replace(' ', '')
        candidates.extend([
            f"{clean_name}.com",
            f"{clean_name}.ai",
            f"{clean_name}.io",
        ])
        
        # Variation 2: Hyphenated
        hyphen_name = name.replace(' ', '-')
        candidates.extend([
            f"{hyphen_name}.com",
            f"{hyphen_name}.ai",
        ])
        
        # Variation 3: First word only (for "Company Name Inc")
        first_word = name.split()[0]
        if len(name.split()) > 1:
            candidates.extend([
                f"{first_word}.com",
                f"{first_word}.ai",
            ])
        
        # Test each candidate — DNS resolve then verify homepage ownership
        for candidate in candidates:
            if self._test_domain_exists(candidate):
                if self._name_appears_on_homepage(candidate, company_name):
                    return candidate
                # DNS resolves but company name not found — wrong company, skip

        return None

    def _name_appears_on_homepage(self, domain: str, company_name: str) -> bool:
        """
        Verify the company name appears on the resolved domain's homepage.
        Prevents resolving generic dictionary-word domains (e.g. nimble.com)
        to unrelated companies, and detects domain parking pages.
        """
        name_lower = company_name.lower()
        name_slug = name_lower.replace(' ', '')

        # Quick win: if the domain itself contains the company slug, it's likely correct
        # e.g. "slangai.com" for "Slang AI", "grottoai.com" for "Grotto AI"
        domain_stripped = domain.replace('www.', '').split('.')[0]
        if name_slug in domain_stripped or domain_stripped in name_slug:
            # Still need to check it's not a parking page — fetch and check redirect
            try:
                r = requests.get(
                    f'https://{domain}', timeout=6,
                    headers={'User-Agent': 'Mozilla/5.0'}, allow_redirects=True, verify=False
                )
                final_host = urlparse(r.url).netloc.replace('www.', '')
                if any(p in final_host for p in self.reject_patterns):
                    return False
            except Exception:
                pass
            return True

        try:
            r = requests.get(
                f'https://{domain}', timeout=6,
                headers={'User-Agent': 'Mozilla/5.0'}, allow_redirects=True, verify=False
            )
            # Reject if redirected to a known parking/broker site
            final_host = urlparse(r.url).netloc.replace('www.', '')
            if any(p in final_host for p in self.reject_patterns):
                return False
            # Check company name presence on page (slug or space-separated)
            page_lower = r.text.lower()
            return name_slug in page_lower or name_lower in page_lower
        except Exception:
            return False  # can't verify → fall through to AI search

    def _test_domain_exists(self, domain: str) -> bool:
        """
        Test if domain exists using DNS lookup
        
        Returns: True if domain has A or CNAME records
        """
        try:
            # Try A record
            dns.resolver.resolve(domain, 'A')
            return True
        except:
            try:
                # Try CNAME record
                dns.resolver.resolve(domain, 'CNAME')
                return True
            except:
                return False
    
    def _ai_search(self, company_name: str) -> Optional[str]:
        """
        Stage 3: Use Claude with web search to find official website
        
        Last resort when deterministic methods fail
        """
        prompt = f"""Find the official website for the company "{company_name}".

Requirements:
1. Must be the company's official website (not LinkedIn, Crunchbase, social media)
2. Must be the root domain (e.g., "company.com" not "linkedin.com/company/...")
3. Verify the domain actually exists

Return ONLY the domain in format: "company.com"

If you cannot find a valid official website, return: "NOT_FOUND"
"""

        try:
            message = self.anthropic_client.messages.create(
                model="claude-sonnet-4-20250514",
                max_tokens=500,
                tools=[{
                    "type": "web_search_20250305",
                    "name": "web_search"
                }],
                messages=[{"role": "user", "content": prompt}]
            )
            
            response = message.content[0].text.strip()
            
            # Clean response
            response = response.replace('https://', '').replace('http://', '').strip()
            response = response.split()[0]  # Take first word
            response = response.rstrip('.,;')  # Remove trailing punctuation
            
            if response == "NOT_FOUND":
                return None
            
            # Validate
            if self._is_valid_domain(response):
                return response
            
            return None
            
        except Exception as e:
            print(f"  ⚠️  AI search failed: {e}")
            return None
    
    def _is_valid_domain(self, domain: str) -> bool:
        """
        Validate domain format and reject unwanted domains
        
        Rules:
        - Must match domain pattern (word.tld)
        - Cannot be social media or aggregator
        - Must be root domain (not subdomain)
        """
        # Basic format check
        if not re.match(r'^[a-z0-9-]+\.[a-z]{2,}$', domain.lower()):
            return False
        
        # Reject unwanted domains
        for pattern in self.reject_patterns:
            if pattern in domain.lower():
                return False
        
        # Reject common false positives
        if domain.startswith('www.'):
            domain = domain[4:]
        
        # Reject subdomains (must be root domain)
        parts = domain.split('.')
        if len(parts) > 2:
            # Allow some exceptions like "co.uk"
            if not (parts[-2] in ['co', 'com', 'net'] and parts[-1] in ['uk', 'au', 'nz']):
                return False
        
        return True
    
    def verify_domain(self, domain: str) -> dict:
        """
        Verify domain is accessible and get metadata
        
        Returns:
        {
            'accessible': bool,
            'redirects_to': Optional[str],
            'status_code': int,
            'title': Optional[str]
        }
        """
        result = {
            'accessible': False,
            'redirects_to': None,
            'status_code': None,
            'title': None
        }
        
        try:
            response = requests.get(
                f'https://{domain}',
                timeout=10,
                allow_redirects=True,
                headers={'User-Agent': 'Mozilla/5.0'}
            )
            
            result['accessible'] = response.status_code == 200
            result['status_code'] = response.status_code
            
            # Check for redirects
            if response.url != f'https://{domain}' and response.url != f'https://{domain}/':
                final_domain = urlparse(response.url).netloc
                result['redirects_to'] = final_domain.replace('www.', '')
            
            # Extract title
            if '<title>' in response.text:
                title = re.search(r'<title>(.*?)</title>', response.text, re.IGNORECASE)
                if title:
                    result['title'] = title.group(1).strip()
            
        except Exception as e:
            print(f"  ⚠️  Verification failed: {e}")
        
        return result


# ============================================================================
# EXAMPLE USAGE
# ============================================================================

def example_usage():
    """Show how to use the domain resolver"""
    
    resolver = DomainResolver()
    
    # Example 1: From article text
    article = """
    Anthropic, the AI safety startup, announced $500M in funding today.
    Visit anthropic.com to learn more about their research.
    """
    
    domain = resolver.resolve("Anthropic", article)
    print(f"Result: {domain}")
    
    # Example 2: Just company name (will try DNS guessing)
    domain = resolver.resolve("OpenAI")
    print(f"Result: {domain}")
    
    # Example 3: Verify domain
    if domain:
        verification = resolver.verify_domain(domain)
        print(f"Verification: {verification}")


if __name__ == "__main__":
    example_usage()
