# Feature Spec: Vertical Classification & Cloud Propensity Tagging

## Overview

Add vertical and sub-vertical classification to every company in the pipeline, and derive a Cloud Propensity tag (High / Medium / Low) for each company based on its classified sub-vertical.

This is a derived intelligence layer that sits on top of the existing attribution engine. It does not replace or modify any existing attribution logic — it adds new fields to the company record and surfaces them in the dashboard.

---

## Context & Design Decisions

### Why tags, not scores

Cloud propensity is a judgment call, not a measurement. Using a numeric score (e.g. 0.87) implies false precision that doesn't exist in the underlying signal. A three-tier tag (High / Medium / Low) is more honest about what it is and more useful for a GTM audience who needs to make a call, not analyze a decimal.

Confidence scoring already exists for attribution (cloud_confidence, ai_confidence). The propensity tag is a separate concept — it reflects the *structural likelihood* of a startup in a given sub-vertical becoming a significant cloud customer, independent of what they currently use.

### Classification approach

Classification should be LLM-based, using the company's name, domain, funding announcement text, and any existing metadata (investors, founder background from the Add Company flow) as input. The LLM maps the company to the closest vertical + sub-vertical from the taxonomy.

Do NOT use a rules-based keyword classifier. The taxonomy has 84 sub-verticals across 17 verticals — keyword matching will produce noisy results for ambiguous company names. The LLM is better at reasoning about "what kind of company is this" from a short description.

### Propensity is derived from taxonomy, not per-company

The Cloud Propensity tag is a property of the sub-vertical, not individually assessed per company. Once a company is classified into a sub-vertical, its propensity tag is looked up from the taxonomy table. This keeps the logic simple, consistent, and editable without re-running the pipeline.

---

## Taxonomy

The full vertical/sub-vertical/propensity taxonomy is defined in `Startup_Vertical_Taxonomy_v2.xlsx` and should be hardcoded as a Python dictionary in a new file `app/taxonomy.py`.

### Structure

```python
# app/taxonomy.py

TAXONOMY = {
    "AI Infrastructure & Compute": {
        "propensity": "High",  # vertical-level default (dominant)
        "sub_verticals": {
            "Foundational model builders":                        "High",
            "AI Hardware Accelerators / Specialized Compute":    "Medium",
            "MLOps / Training platforms":                        "High",
            "Vector databases":                                  "High",
        }
    },
    "AI Applications & Tooling": {
        "propensity": "High",
        "sub_verticals": {
            "AI Development Tools & Agent frameworks":           "High",
            "Vertical-specific AI apps":                         "High",
        }
    },
    "B2B SaaS / Enterprise": {
        "propensity": "High",
        "sub_verticals": {
            "API-first services":                                "High",
            "Data-analytics platforms":                          "High",
            "CRM":                                               "Medium",
            "Collaboration / comms":                             "Medium",
            "Project-management tools":                          "Medium",
            "Endpoint security":                                 "Medium",
        }
    },
    "Climate & Energy Tech": {
        "propensity": "Medium",
        "sub_verticals": {
            "Grid-management software":                          "High",
            "Carbon-capture process compute":                    "High",
            "Carbon-accounting SaaS":                            "High",
            "EV-charging infra SW":                              "Medium",
            "Precision-agriTech":                                "Medium",
            "Sustainable-materials R&D":                         "Medium",
            "Fusion & advanced nuclear":                         "Medium",
            "Geothermal & subsurface looping":                   "Low",
            "Utility-scale solar & wind EPC":                    "Low",
            "Next-gen battery tech & Li supply-chain":           "Low",
        }
    },
    "Consumer / E-commerce & Marketplaces": {
        "propensity": "High",
        "sub_verticals": {
            "Travel / leisure marketplaces":                     "High",
            "E-commerce enablers":                               "High",
            "Social media & consumer apps":                      "High",
            "Streaming & Immersive Media":                       "High",
            "Connected Consumer Hardware & IoT":                 "Medium",
            "DTC infra-platforms":                               "Medium",
        }
    },
    "Cybersecurity": {
        "propensity": "High",
        "sub_verticals": {
            "Data-security / privacy / vaulting":               "High",
            "CSPM / CNAPP":                                      "High",
            "Threat Detection & Response / SIEM":               "High",
            "IAM & auth":                                        "High",
            "Endpoint security":                                 "Medium",
        }
    },
    "Data Infrastructure": {
        "propensity": "High",
        "sub_verticals": {
            "Data pipelines & ETL":                              "High",
            "Lakehouse & data warehouse":                        "High",
            "Reverse ETL & data activation":                     "High",
            "Data quality & observability":                      "High",
        }
    },
    "Developer Tools": {
        "propensity": "High",
        "sub_verticals": {
            "CI/CD & DevOps platforms":                          "High",
            "Observability & monitoring":                        "High",
            "API management & gateways":                         "High",
            "Security tooling for developers":                   "High",
        }
    },
    "Education Tech": {
        "propensity": "Medium",
        "sub_verticals": {
            "AI tutoring & adaptive learning":                   "High",
            "Learning management platforms":                     "Medium",
            "Credentialing & skills platforms":                  "Medium",
        }
    },
    "Fintech, Payments and Crypto": {
        "propensity": "High",
        "sub_verticals": {
            "LendingTech":                                       "High",
            "Investment Tech":                                   "High",
            "Digital Banking / Neobanks":                        "High",
            "Payment Processing":                                "High",
            "RegTech & compliance":                              "High",
            "InsurTech":                                         "High",
            "Crypto / Web3 infra":                              "Low",
            "DeFi":                                              "Low",
        }
    },
    "Healthcare, BioTech & Life Sciences": {
        "propensity": "High",
        "sub_verticals": {
            "AI drug discovery":                                 "High",
            "Health-data analytics":                             "High",
            "Genomics & bioinformatics":                         "High",
            "Medical-device IoT platforms":                      "High",
            "Tele-health platforms":                             "High",
            "Digital therapeutics (DTx)":                       "Medium",
            "Cell & gene therapy manufacturing":                 "Medium",
            "Bio-foundries & wet-lab platforms":                 "Medium",
            "Oncology & cancer immunotherapy":                   "Medium",
        }
    },
    "HR Tech / Workforce Tech": {
        "propensity": "High",
        "sub_verticals": {
            "Recruiting & talent intelligence":                  "High",
            "Workforce analytics":                               "High",
            "Skills & learning platforms":                       "Medium",
            "Payroll & benefits platforms":                      "Medium",
        }
    },
    "Industrial / IoT / Robotics": {
        "propensity": "High",
        "sub_verticals": {
            "Robotics control platforms":                        "Medium",
            "Warehouse / logistics platforms":                   "High",
            "Industrial IoT platforms":                          "High",
            "Process-optimisation SW":                           "High",
            "Digital twins":                                     "High",
            "Predictive maintenance":                            "High",
        }
    },
    "Legal Tech": {
        "propensity": "High",
        "sub_verticals": {
            "Contract analysis & management":                    "High",
            "E-discovery & litigation support":                  "High",
            "Regulatory compliance automation":                  "High",
        }
    },
    "Aero / Defence / Space": {
        "propensity": "Medium",
        "sub_verticals": {
            "Sat-image analytics":                               "High",
            "Autonomous systems":                                "Medium",
            "Mobility & Transportation Tech":                    "Medium",
            "Launch-systems software":                           "Low",
        }
    },
    "PropTech / Real Estate Tech": {
        "propensity": "Medium",
        "sub_verticals": {
            "Property data & valuation platforms":               "High",
            "Smart building & IoT platforms":                    "Medium",
            "Real estate transaction platforms":                 "Medium",
        }
    },
    "Construction Tech / AEC": {
        "propensity": "High",
        "sub_verticals": {
            "AI project management & scheduling":                "High",
            "BIM & digital design platforms":                    "High",
            "Safety monitoring & site IoT":                      "Medium",
        }
    },
}

# Flat lookup: sub_vertical -> propensity
SUB_VERTICAL_PROPENSITY: dict[str, str] = {}
for vertical_data in TAXONOMY.values():
    for sv, prop in vertical_data["sub_verticals"].items():
        SUB_VERTICAL_PROPENSITY[sv] = prop

# Valid values
VALID_VERTICALS = list(TAXONOMY.keys())
VALID_PROPENSITY = ["High", "Medium", "Low"]
```

---

## Database Changes

### New columns on `attribution_snapshots`

```sql
ALTER TABLE attribution_snapshots
  ADD COLUMN IF NOT EXISTS vertical          TEXT,
  ADD COLUMN IF NOT EXISTS sub_vertical      TEXT,
  ADD COLUMN IF NOT EXISTS cloud_propensity  TEXT CHECK (cloud_propensity IN ('High', 'Medium', 'Low')),
  ADD COLUMN IF NOT EXISTS classification_confidence  TEXT CHECK (classification_confidence IN ('high', 'medium', 'low')),
  ADD COLUMN IF NOT EXISTS classification_source      TEXT;
```

**Field definitions:**

| Field | Type | Description |
|---|---|---|
| `vertical` | TEXT | Top-level vertical from taxonomy (e.g. "AI Applications & Tooling") |
| `sub_vertical` | TEXT | Sub-vertical from taxonomy (e.g. "Vertical-specific AI apps") |
| `cloud_propensity` | TEXT | Derived from sub_vertical lookup: High / Medium / Low |
| `classification_confidence` | TEXT | LLM's self-reported confidence: high / medium / low |
| `classification_source` | TEXT | How it was classified: "llm_classification" or "manual" |

---

## New Module: `app/classification/classifier.py`

Handles LLM-based vertical classification.

### Function signature

```python
async def classify_company(
    company_name: str,
    domain: str,
    description: str,           # from funding announcement text
    investors: list[str] | None = None,
    founder_background: str | None = None,
) -> ClassificationResult:
    ...

@dataclass
class ClassificationResult:
    vertical: str
    sub_vertical: str
    cloud_propensity: str           # derived from taxonomy lookup
    classification_confidence: str  # "high" / "medium" / "low"
    classification_source: str      # "llm_classification"
    reasoning: str                  # LLM's one-line rationale (for debugging)
```

### LLM prompt design

Use **Claude Haiku** (cheapest, fast enough for classification).

The prompt must:
1. Provide the full list of valid vertical + sub-vertical combinations
2. Ask for a single best-fit classification with a confidence rating
3. Request JSON output only — no prose
4. Include the company's name, domain, funding description, and any available context

```python
CLASSIFICATION_PROMPT = """
You are classifying a startup into a vertical and sub-vertical taxonomy for cloud provider intelligence.

Company: {company_name}
Domain: {domain}
Description: {description}
Investors: {investors}
Founder background: {founder_background}

Choose the single best-fit vertical and sub-vertical from this taxonomy:

{taxonomy_list}

Rules:
- Choose the most specific sub-vertical that fits
- If the company could fit multiple, choose the one most relevant to their PRIMARY product
- If genuinely unclear, choose the closest match and set confidence to "low"

Respond with JSON only, no other text:
{{
  "vertical": "<exact vertical name>",
  "sub_vertical": "<exact sub-vertical name>",
  "confidence": "high" | "medium" | "low",
  "reasoning": "<one sentence explaining the classification>"
}}
"""
```

### Taxonomy list formatting for prompt

```python
def _format_taxonomy_for_prompt() -> str:
    lines = []
    for vertical, data in TAXONOMY.items():
        lines.append(f"\n{vertical}:")
        for sv in data["sub_verticals"]:
            lines.append(f"  - {sv}")
    return "\n".join(lines)
```

### Validation

After parsing the LLM response:
- Verify `vertical` is in `VALID_VERTICALS` — if not, set both to `None` and log
- Verify `sub_vertical` is in the vertical's sub_verticals — if not, try to fuzzy-match or set to `None`
- Derive `cloud_propensity` from `SUB_VERTICAL_PROPENSITY[sub_vertical]`

---

## Pipeline Integration

### Where to call the classifier

In `pipeline.py`, inside `_build_snapshot()`, after domain resolution and before or alongside the attribution engine call. Classification is independent of attribution — they can run concurrently.

```python
# pipeline.py — inside _build_snapshot()

classification_task = asyncio.create_task(
    classify_company(
        company_name=company.name,
        domain=company.domain,
        description=company.description,
        investors=company.investors,
        founder_background=company.founder_background,
    )
)

attribution_task = asyncio.create_task(
    attribution_engine.attribute(company)
)

classification, attribution = await asyncio.gather(
    classification_task, attribution_task
)

snapshot = {
    # ... existing attribution fields ...
    "vertical":                   classification.vertical,
    "sub_vertical":               classification.sub_vertical,
    "cloud_propensity":           classification.cloud_propensity,
    "classification_confidence":  classification.classification_confidence,
    "classification_source":      classification.classification_source,
}
```

### Backfilling existing records

After deploying, run a one-off backfill script to classify all existing companies that have `vertical IS NULL`.

```python
# scripts/backfill_classification.py

async def backfill():
    # Fetch all companies with null vertical
    # For each: fetch their name, domain, description from attribution_snapshots
    # Run classify_company()
    # PATCH the record with the result
    # Log any failures
```

Run this synchronously (not as a background task) to avoid Railway cron overlap issues.

---

## Add Company Flow Update

The `Add Company` form already accepts `founder_background` and `investors` — pass both through to `classify_company()` when the manual enrichment flow runs. This gives the classifier better signal for edge cases.

---

## Dashboard Changes

### Companies table — new columns

Add two columns to the companies table in `dashboard/app/companies/page.tsx`:

| Column | Display | Notes |
|---|---|---|
| Vertical | Text | Show full vertical name. Truncate if needed with tooltip |
| Sub-Vertical | Text | Show full sub-vertical name |
| Cloud Propensity | Chip | Green = High, Amber = Medium, Orange = Low |

### New filter: Cloud Propensity

Add a dropdown filter alongside the existing cloud/AI provider filters:

```
All Propensity ▼   →   High / Medium / Low
```

### New filter: Vertical

Add a vertical dropdown filter. Populated dynamically from `VALID_VERTICALS`.

### Dashboard summary cards

Add one new summary card to the main Dashboard view:

```
TOP VERTICAL
[most common vertical among tracked companies]
[N startups]
```

### Propensity breakdown chart (optional, v2)

A bar chart showing count of High / Medium / Low companies in the tracked set. Useful for showing portfolio composition at a glance.

---

## Models Update

Add to `app/models.py`:

```python
from enum import Enum

class CloudPropensity(str, Enum):
    HIGH   = "High"
    MEDIUM = "Medium"
    LOW    = "Low"

class ClassificationConfidence(str, Enum):
    HIGH   = "high"
    MEDIUM = "medium"
    LOW    = "low"

class CompanyClassification(BaseModel):
    vertical:                  str | None
    sub_vertical:              str | None
    cloud_propensity:          CloudPropensity | None
    classification_confidence: ClassificationConfidence | None
    classification_source:     str | None
```

---

## Error Handling & Edge Cases

**Ambiguous company** — LLM returns low confidence. Accept the classification but store `classification_confidence = "low"`. Do not block the pipeline. Surface confidence in the dashboard (could show a faded chip or tooltip).

**LLM returns invalid vertical/sub-vertical** — Validate response. If invalid, store `vertical = None`, `sub_vertical = None`, `cloud_propensity = None`. Log for review. Do not retry automatically (cost control).

**New company type not in taxonomy** — The taxonomy will need periodic updates. When `classification_confidence = "low"` clusters around a particular type of company, that's a signal to add a new sub-vertical. Review quarterly.

**Manual override** — The `classification_source` field supports `"manual"` as a value. Build a simple override endpoint or direct Supabase edit path so you can correct misclassifications without re-running the pipeline.

---

## Build Order

1. Create `app/taxonomy.py` with full TAXONOMY dict
2. Run SQL migration to add new columns
3. Build `app/classification/classifier.py` with prompt, API call, validation
4. Add unit tests for classifier with 5-6 representative company examples
5. Integrate into `pipeline.py` via `asyncio.gather`
6. Run backfill script on existing records
7. Add vertical/sub-vertical/propensity columns to dashboard Companies table
8. Add propensity and vertical filter dropdowns
9. Add Top Vertical summary card to Dashboard

---

## Cost Estimate

Claude Haiku pricing: ~$0.25 / 1M input tokens, ~$1.25 / 1M output tokens.

Each classification call: ~800 tokens input (taxonomy list + company context) + ~100 tokens output = ~$0.0002 per company.

At 30 companies/day: ~$0.006/day, ~$2.20/year. Negligible.

---

## Notes for Claude Code

- The taxonomy in `app/taxonomy.py` is the single source of truth. The dashboard filter options, the LLM prompt's valid values, and the propensity lookup should all derive from this dict — never hardcode values elsewhere.
- Keep the classifier stateless and independently testable. It should work with just a company name + domain if no other context is available.
- The `reasoning` field from the LLM is for debugging only — do not surface it in the dashboard, but do log it to help identify taxonomy gaps over time.
- Do not add vertical/propensity to the pipeline's Brave or Perplexity search queries — classification is a separate layer and should not influence signal gathering.
