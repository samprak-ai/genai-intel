# Open Source Signal Sources for Cloud & AI Attribution

## 🎯 High-Value Signals (Already Planned)

### Tier 1: Infrastructure Signals (STRONG - 1.0)
- ✅ DNS/CNAME Records - Shows actual hosting
- ✅ HTTP Headers - Reveals CDN and cloud provider
- ✅ SSL Certificate Issuer - Often cloud-managed
- ✅ Partnership Press Releases - Official announcements

### Tier 2: Content Signals (MEDIUM - 0.6)
- ✅ Job Postings - Requirements reveal tech stack
- ✅ Trust/Security Pages - Often mention infrastructure
- ✅ Technical Blog Posts - Engineering team writing
- ✅ API Documentation - Shows integrations

---

## 💎 Additional Open Source Signals (New Ideas)

### GitHub Repository Analysis (STRONG - 1.0)
**If company has public repos:**

```python
# What to look for:
1. Infrastructure-as-Code Files:
   - terraform/*.tf → Check for AWS/GCP/Azure providers
   - cloudformation/*.yaml → AWS
   - deployment.yaml → Check "image: gcr.io" (GCP) vs "image: *.azurecr.io" (Azure)

2. CI/CD Configuration:
   - .github/workflows/*.yml → Check for AWS/GCP/Azure actions
   - .circleci/config.yml → Deployment targets
   - Jenkinsfile → Deployment scripts

3. Dependency Files:
   - package.json → Look for "@aws-sdk", "@google-cloud", "@azure"
   - requirements.txt → Look for "boto3" (AWS), "google-cloud-*", "azure-*"
   - go.mod → Look for cloud SDKs
   
4. Docker Files:
   - Dockerfile → Base images often reveal cloud (e.g., "FROM gcr.io/...")
   - docker-compose.yml → Service configurations

5. README.md:
   - Deployment instructions
   - "Deploy to AWS/GCP/Azure" badges
   - Infrastructure requirements
```

**Reliability:** HIGH - Code doesn't lie
**Availability:** Only if company open-sources
**Weight:** 1.0 (STRONG)

---

### Security.txt / Vulnerability Disclosure (MEDIUM - 0.6)
**Check:** `https://company.com/.well-known/security.txt`

```
# Often mentions:
Contact: security@company.com
Encryption: https://keys.company.com/pgp.txt
Acknowledgments: https://company.com/security/hall-of-fame
Policy: https://company.com/security-policy

# Sometimes reveals:
"Infrastructure hosted on AWS"
"Report vulnerabilities in our GCP environment"
```

**Reliability:** MEDIUM - When present, usually accurate
**Availability:** ~30% of startups have this
**Weight:** 0.6 (MEDIUM)

---

### Robots.txt Analysis (WEAK - 0.3)
**Check:** `https://company.com/robots.txt`

```
# May reveal CDN or hosting patterns:
User-agent: *
Sitemap: https://company-cdn.cloudfront.net/sitemap.xml  # → AWS
Sitemap: https://storage.googleapis.com/company/sitemap.xml  # → GCP

# Or API endpoints:
Disallow: /api/
# Then check API subdomain
```

**Reliability:** WEAK - Indirect evidence
**Availability:** Most sites have this
**Weight:** 0.3 (WEAK)

---

### Technology Stack Detectors (MEDIUM - 0.6)
**Free Services:**

1. **BuiltWith** (Free Tier)
   - URL: `https://builtwith.com/company.com`
   - Shows: Hosting provider, CDN, analytics
   - Limitation: Rate limited on free tier

2. **Wappalyzer** (Open Source)
   - Can run locally
   - Detects: Frameworks, hosting, CDN
   - Reliability: MEDIUM

3. **WhatRuns** (Browser Extension)
   - Manual but accurate
   - Shows full tech stack

**Reliability:** MEDIUM - Automated detection can miss things
**Weight:** 0.6 (MEDIUM)

---

### IP Address Geolocation & ASN Lookup (STRONG - 1.0)
**Process:**
```python
1. Resolve company.com → IP address
2. Lookup IP ownership (ASN - Autonomous System Number)
3. Check against known cloud ranges:
   - AWS: AS16509, AS14618
   - GCP: AS15169, AS396982
   - Azure: AS8075
   - Cloudflare: AS13335 (not a cloud provider!)
```

**Example:**
```
company.com → 52.84.123.45
52.84.123.45 → AS16509 (Amazon)
Conclusion: Hosted on AWS
```

**Reliability:** HIGH - IP ownership is public record
**Availability:** 100% (every website has an IP)
**Weight:** 1.0 (STRONG)
**Note:** Can be obscured by CDN (Cloudflare, Fastly)

---

### LinkedIn Company Page (WEAK - 0.3)
**What to check:**

1. **Company Posts:**
   - "Excited to announce our partnership with AWS!"
   - "Join us at Google Cloud Next!"
   - Photos from cloud provider events

2. **Employee Titles:**
   - "AWS Solutions Architect"
   - "GCP Cloud Engineer"
   - Count mentions in job titles

3. **Company Specialties:**
   - Sometimes lists "AWS Partner" or "Google Cloud Partner"

**Reliability:** WEAK - Marketing content, not technical proof
**Availability:** Most companies have LinkedIn
**Weight:** 0.3 (WEAK)

---

### Podcast/Conference Appearances (WEAK - 0.3)
**Check:**
- YouTube: "Company Name AWS" / "Company Name GCP"
- Podcast: Company representatives on cloud provider podcasts
- Conference: Speaking at AWS re:Invent, Google Cloud Next, Microsoft Build

**Reliability:** WEAK - Attendance doesn't prove usage
**Weight:** 0.3 (WEAK)

---

### Product Hunt / Hacker News Mentions (WEAK - 0.3)
**Search:**
- "Company Name" on Hacker News
- Read launch announcements
- Check "Show HN" posts for tech stack details

**Reliability:** WEAK - Sometimes founders share tech details
**Weight:** 0.3 (WEAK)

---

### Case Studies & Customer Stories (STRONG - 1.0)
**Cloud Provider Sites:**
- aws.amazon.com/solutions/case-studies/
- cloud.google.com/customers
- azure.microsoft.com/en-us/case-studies/

**AI Provider Sites:**
- openai.com/customer-stories
- anthropic.com/customers (when available)
- cohere.com/customers

**Reliability:** VERY HIGH - Official endorsement
**Availability:** Only featured customers (~5% of startups)
**Weight:** 1.0 (STRONG)

---

### Certificate Transparency Logs (MEDIUM - 0.6)
**Check:** crt.sh or similar

```
Search for: company.com
Look at certificates for:
- *.company.com certificates
- Issuer patterns
- SANs (Subject Alternative Names) that reveal subdomains

Example findings:
- api-prod.us-west-2.company.com → AWS region hint
- storage.company.com → May point to cloud storage
```

**Reliability:** MEDIUM - Reveals infrastructure patterns
**Weight:** 0.6 (MEDIUM)

---

### Wayback Machine (Archive.org) (WEAK - 0.3)
**Use case:**
- See historical versions of company website
- Check when cloud provider mentions appeared
- Track infrastructure changes over time

**Reliability:** WEAK - Historical, not current
**Weight:** 0.3 (WEAK)

---

## 📊 Recommended Signal Priority

### Must Implement (High ROI):
1. ✅ DNS/CNAME records - Easy, highly reliable
2. ✅ HTTP headers - Easy, highly reliable
3. ✅ IP/ASN lookup - Easy, highly reliable
4. ✅ Job postings - Moderate effort, good reliability
5. ✅ GitHub repo analysis - Moderate effort, very reliable when available
6. ✅ Case studies - Easy to check, very reliable when available

### Nice to Have (Medium ROI):
7. ⭐ Trust/Security pages - Easy, moderate reliability
8. ⭐ Security.txt - Easy, moderate reliability
9. ⭐ Certificate transparency - Moderate effort, moderate reliability

### Low Priority (Low ROI):
10. 💤 LinkedIn scraping - Effort vs. value doesn't justify
11. 💤 Podcast appearances - Too indirect
12. 💤 Wayback Machine - Historical, not actionable

---

## 🎯 Final Recommendation: Signal Stack

### For Cloud Attribution:
```python
PRIORITY_ORDER = [
    # Deterministic (always check these)
    "partnership_override",      # 100% reliable
    "ip_asn_lookup",            # 95% reliable
    "dns_cname",                # 95% reliable  
    "http_headers",             # 90% reliable
    "case_study",               # 100% reliable (when exists)
    
    # Heuristic (check if time permits)
    "github_repos",             # 90% reliable (when public)
    "job_postings",             # 70% reliable
    "trust_pages",              # 60% reliable
    "security_txt",             # 60% reliable
    
    # Last resort
    "llm_inference",            # 50% reliable (educated guess)
]
```

### For AI Attribution:
```python
PRIORITY_ORDER = [
    # Deterministic
    "partnership_override",      # 100% reliable
    "case_study",               # 100% reliable (when exists)
    "api_docs",                 # 90% reliable (shows integrations)
    
    # Heuristic  
    "github_repos",             # 80% reliable (check imports)
    "job_postings",             # 70% reliable
    "tech_blog",                # 70% reliable
    
    # Last resort
    "llm_inference",            # 50% reliable
]
```

---

## 💡 Implementation Strategy

### Phase 1: Core Deterministic (Week 1)
- DNS/CNAME checks
- IP/ASN lookup
- HTTP headers
- Partnership overrides

### Phase 2: GitHub & Jobs (Week 2)
- GitHub repository analysis (if public)
- Job posting scraping (LinkedIn/Indeed)

### Phase 3: Content Analysis (Week 3)
- Trust/security pages
- API documentation
- Technical blogs

### Phase 4: AI Fallback (Week 4)
- LLM inference for unknowns
- Tie-breaking logic

**Each week adds more signals while maintaining deterministic-first philosophy.**
