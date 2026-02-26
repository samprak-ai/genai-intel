"""
Core Pydantic models for GenAI-Intel
Ensures type safety and data validation throughout the pipeline
"""

from pydantic import BaseModel, Field, field_validator
from datetime import date, datetime
from typing import Optional, Literal
from enum import Enum
import re


class ProviderType(str, Enum):
    """Type of provider being attributed"""
    CLOUD = "cloud"
    AI = "ai"


class SignalStrength(str, Enum):
    """Strength of attribution signal"""
    STRONG = "STRONG"    # Partnership, DNS records, official docs
    MEDIUM = "MEDIUM"    # Job postings, tech blogs
    WEAK = "WEAK"        # Website mentions, indirect evidence


class EntrenchmentLevel(str, Enum):
    """How entrenched a startup is with a provider"""
    STRONG = "STRONG"      # Score >= 2.0 (multiple strong signals)
    MODERATE = "MODERATE"  # Score >= 1.0 (one strong or multiple medium)
    WEAK = "WEAK"          # Score >= 0.3 (only weak signals)
    UNKNOWN = "UNKNOWN"    # Score < 0.3 (no evidence)


# ============================================================================
# FUNDING DISCOVERY MODELS
# ============================================================================

class FundingEvent(BaseModel):
    """Structured funding event extracted from articles"""
    
    company_name: str = Field(..., description="Official company name")
    funding_amount_usd: int = Field(..., description="Amount in millions USD")
    funding_round: str = Field(..., description="Seed, Series A, B, C, etc.")
    funding_date: Optional[date] = Field(None, description="When funding closed")
    announcement_date: date = Field(..., description="When announced publicly")
    lead_investors: list[str] = Field(default_factory=list, description="Lead investors")
    founder_background: list[str] = Field(default_factory=list, description="Founder prior employer keywords, e.g. ['Google Brain', 'DeepMind']")
    website: Optional[str] = Field(None, description="Company website if mentioned in article")
    industry: Optional[str] = Field(None, description="Industry/sector")
    description: Optional[str] = Field(None, description="What the company does")
    
    # Source tracking
    source_name: str = Field(..., description="techcrunch, crunchbase, etc.")
    source_url: str = Field(..., description="URL of announcement")
    raw_article_text: Optional[str] = Field(None, description="Full article text")
    
    @field_validator('funding_amount_usd')
    @classmethod
    def validate_funding_amount(cls, v: int) -> int:
        if v < 0:
            raise ValueError("Funding amount must not be negative")
        if v > 1000000:  # > $1T seems wrong
            raise ValueError("Funding amount unrealistically high")
        return v
    
    @field_validator('website')
    @classmethod
    def validate_website(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return v
        
        # Remove protocol if present
        v = v.replace('https://', '').replace('http://', '').strip('/')
        
        # Reject social media and aggregator sites
        reject_patterns = [
            'linkedin.com',
            'twitter.com',
            'x.com',
            'facebook.com',
            'instagram.com',
            'crunchbase.com',
            'pitchbook.com'
        ]
        
        for pattern in reject_patterns:
            if pattern in v.lower():
                return None
        
        # Basic domain validation
        if not re.match(r'^[a-z0-9-]+\.[a-z]{2,}$', v.lower()):
            return None
        
        return v
    
    @field_validator('funding_round')
    @classmethod
    def normalize_funding_round(cls, v: str) -> str:
        """Normalize funding round names"""
        v = v.upper().strip()
        
        # Map variations to standard names
        round_map = {
            'SEED': 'Seed',
            'PRE-SEED': 'Pre-Seed',
            'SERIES A': 'Series A',
            'SERIES B': 'Series B',
            'SERIES C': 'Series C',
            'SERIES D': 'Series D',
            'SERIES E': 'Series E',
        }
        
        # Try exact match
        if v in round_map:
            return round_map[v]
        
        # Try partial match
        for key, value in round_map.items():
            if key in v:
                return value
        
        return v  # Return as-is if no match


# ============================================================================
# ATTRIBUTION MODELS
# ============================================================================

class AttributionSignal(BaseModel):
    """A single piece of evidence for cloud/AI provider attribution"""
    
    provider_type: ProviderType = Field(..., description="cloud or ai")
    provider_name: str = Field(..., description="AWS, GCP, Azure, OpenAI, etc.")
    signal_source: str = Field(..., description="dns, partnership, jobs, website, etc.")
    signal_strength: SignalStrength = Field(..., description="STRONG, MEDIUM, WEAK")
    
    # Evidence details
    evidence_text: Optional[str] = Field(None, description="The actual evidence found")
    evidence_url: Optional[str] = Field(None, description="Where evidence was found")
    confidence_weight: float = Field(..., description="Numerical weight (1.0, 0.6, 0.3)")
    
    collected_at: datetime = Field(default_factory=datetime.now)
    
    @field_validator('confidence_weight')
    @classmethod
    def validate_weight(cls, v: float) -> float:
        if v not in [1.0, 0.6, 0.3]:
            raise ValueError("Weight must be 1.0, 0.6, or 0.3")
        return v


class ProviderEntry(BaseModel):
    """A single provider with its role and confidence"""
    provider_name: str
    role: str = Field(..., description="e.g. 'Cloud infrastructure', 'AI service provider'")
    confidence: float = Field(..., ge=0.0, le=1.0)
    entrenchment: EntrenchmentLevel
    raw_score: float
    signals: list[AttributionSignal] = Field(default_factory=list)


class Attribution(BaseModel):
    """
    Attribution result — supports single provider OR multi-cloud/multi-AI

    Examples:
      Single:       primary="AWS",  is_multi=False, providers=[AWS]
      Multi-cloud:  primary=None,   is_multi=True,  providers=[AWS, GCP]
      Multi-AI:     primary="OpenAI", is_multi=True, providers=[OpenAI, Anthropic]
      N/A:          is_not_applicable=True, note set — company is itself a provider
                    or attribution is structurally not meaningful (e.g. GPU neocloud)
    """

    provider_type: ProviderType

    # Multi-cloud aware fields
    is_multi: bool = Field(False, description="True if multiple providers detected")
    primary_provider: Optional[str] = Field(None, description="Clear primary, or None if multi")
    providers: list[ProviderEntry] = Field(default_factory=list, description="All detected providers")

    # Not-applicable flag — set when attribution is structurally meaningless
    # e.g. the company IS a cloud/compute provider, not a consumer of one
    is_not_applicable: bool = Field(False, description="True when cloud/AI attribution is not meaningful")
    not_applicable_note: Optional[str] = Field(None, description="Why attribution is N/A")

    # Overall confidence in the full picture
    confidence: float = Field(..., ge=0.0, le=1.0)
    evidence_count: int = Field(..., ge=0)

    # All supporting signals across all providers
    signals: list[AttributionSignal] = Field(default_factory=list)

    @property
    def provider_names(self) -> list[str]:
        """Convenience: list of all provider names"""
        return [p.provider_name for p in self.providers]

    @property
    def display_name(self) -> str:
        """Human-readable attribution string"""
        if self.is_not_applicable:
            return "Not Applicable"
        if self.is_multi:
            return f"Multi ({', '.join(self.provider_names)})"
        return self.primary_provider or "Unknown"

    @staticmethod
    def calculate_entrenchment(score: float) -> EntrenchmentLevel:
        """Map raw score to entrenchment level"""
        if score >= 2.0:
            return EntrenchmentLevel.STRONG
        elif score >= 1.0:
            return EntrenchmentLevel.MODERATE
        elif score >= 0.3:
            return EntrenchmentLevel.WEAK
        else:
            return EntrenchmentLevel.UNKNOWN


class AttributionSnapshot(BaseModel):
    """Complete attribution snapshot for a startup — supports multi-cloud"""
    
    startup_id: str
    snapshot_date: date = Field(default_factory=date.today)
    
    # Cloud attribution
    cloud_attribution: Optional[Attribution] = None
    
    # AI attribution
    ai_attribution: Optional[Attribution] = None
    
    created_at: datetime = Field(default_factory=datetime.now)
    
    @property
    def cloud_display(self) -> str:
        if not self.cloud_attribution:
            return "Unknown"
        return self.cloud_attribution.display_name  # returns "Not Applicable" when is_not_applicable

    @property
    def ai_display(self) -> str:
        if not self.ai_attribution:
            return "Unknown"
        return self.ai_attribution.display_name  # returns "Not Applicable" when is_not_applicable


# ============================================================================
# STARTUP ENTITY
# ============================================================================

class Startup(BaseModel):
    """Core startup entity"""
    
    id: Optional[str] = None  # UUID from database
    canonical_name: str = Field(..., description="Standardized company name")
    website: str = Field(..., description="Official website domain")
    industry: Optional[str] = None
    description: Optional[str] = None
    
    first_seen: datetime = Field(default_factory=datetime.now)
    last_updated: datetime = Field(default_factory=datetime.now)
    
    @field_validator('canonical_name')
    @classmethod
    def normalize_name(cls, v: str) -> str:
        """Normalize company name for consistency"""
        # Remove common suffixes
        v = re.sub(r'\s+(Inc\.?|LLC|Ltd\.?|Corporation|Corp\.?)$', '', v, flags=re.IGNORECASE)
        
        # Title case
        v = v.strip().title()
        
        return v
    
    @field_validator('website')
    @classmethod
    def validate_website_domain(cls, v: str) -> str:
        """Ensure website is clean domain"""
        # Remove protocol and paths
        v = v.replace('https://', '').replace('http://', '')
        v = v.split('/')[0].strip()
        
        # Must be valid domain format
        if not re.match(r'^[a-z0-9-]+\.[a-z]{2,}$', v.lower()):
            raise ValueError(f"Invalid domain format: {v}")
        
        return v.lower()


# ============================================================================
# PIPELINE MODELS
# ============================================================================

class WeeklyRun(BaseModel):
    """Tracking for weekly pipeline execution"""
    
    id: Optional[str] = None
    run_date: date = Field(default_factory=date.today)
    
    # Metrics
    startups_discovered: int = 0
    startups_attributed: int = 0
    errors_count: int = 0
    execution_time_seconds: Optional[int] = None
    
    # Status
    status: Literal["running", "completed", "failed"] = "running"
    error_log: dict = Field(default_factory=dict)
    
    started_at: datetime = Field(default_factory=datetime.now)
    completed_at: Optional[datetime] = None


# ============================================================================
# CONFIGURATION
# ============================================================================

class SignalWeights:
    """Signal strength to numerical weight mapping"""
    STRONG = 1.0
    MEDIUM = 0.6
    WEAK = 0.3
    
    @classmethod
    def get(cls, strength: SignalStrength) -> float:
        return getattr(cls, strength.value)


# Partnership overrides (deterministic, highest confidence)
# Source: Official press releases or company subprocessors pages only
PARTNERSHIP_OVERRIDES = {
    # AI Labs — cloud overrides only; AI side handled by NOT_APPLICABLE_COMPANIES
    "Anthropic":      {"cloud": ["GCP", "AWS"], "ai": None,
                       "source_url": "https://cloud.google.com/customers/anthropic"},
    "OpenAI":         {"cloud": "Azure",        "ai": None,
                       "source_url": "https://openai.com/index/openai-and-microsoft-extend-partnership/"},
    "Cohere":         {"cloud": "AWS",          "ai": None,
                       "source_url": "https://aws.amazon.com/partners/cohere/"},
    # Verified from official press releases
    "Apptronik":      {"cloud": "GCP",          "ai": None,
                       "source_url": "https://apptronik.com/news-collection/apptronik-partners-with-google-deepmind-robotics"},
    "Axiom Space":    {"cloud": "AWS",          "ai": None,
                       "source_url": "https://www.axiomspace.com/release/amazon-web-services-all-in"},
    "Fundamental AI": {"cloud": "AWS",          "ai": None,
                       "source_url": "https://techfundingnews.com/fundamental-255m-nexus-tabular-ai-oak-hcft-aws/"},
    "Upwind":         {"cloud": "AWS",          "ai": None,
                       "source_url": "https://www.upwind.io/secure-your-aws-environment-with-upwind"},
    "Runway":         {"cloud": "CoreWeave",    "ai": None,
                       "source_url": "https://www.coreweave.com/news/coreweave-announces-agreement-to-power-runways-next-generation-ai-video-models"},
}

# Investor → cloud provider priors
# When a startup has a known VC investor tied to a cloud provider, and no
# stronger deterministic signal exists, we emit a WEAK (0.3) inference signal.
# Logic: portfolio companies often receive infra credits/preferred access from
# their investor's associated cloud. This is a prior, not a certainty.
# Source: Public VC-cloud partnership programmes (e.g. GV → Google Cloud for
# Startups, a16z → multi-cloud, Bessemer → AWS partnership, etc.)
# Companies where cloud/AI attribution is structurally not applicable.
# Used when the company IS itself a cloud/compute provider, builds its own
# foundational models, or otherwise falls outside the consumer-of-cloud frame.
#
# Format: { "Company Name": {"cloud": <note|None>, "ai": <note|None>} }
# Set a field to None to attribute it normally; set to a string to mark N/A.
NOT_APPLICABLE_COMPANIES: dict[str, dict[str, Optional[str]]] = {
    # Frontier AI labs — build proprietary models, not consumers of external AI providers
    "Anthropic": {
        "cloud": None,  # cloud attribution still meaningful (GCP + AWS partnerships)
        "ai":    "Builds proprietary frontier models — not a consumer of external AI providers",
    },
    "OpenAI": {
        "cloud": None,  # cloud attribution still meaningful (Azure partnership)
        "ai":    "Builds proprietary frontier models — not a consumer of external AI providers",
    },
    "Cohere": {
        "cloud": None,  # cloud attribution still meaningful (AWS partnership)
        "ai":    "Builds proprietary frontier models — not a consumer of external AI providers",
    },
    # GPU neoclouds / compute marketplaces — they ARE the infrastructure
    "Pale Blue Dot": {
        "cloud": "GPU neocloud marketplace — is itself the compute provider, not a consumer",
        "ai":    None,   # AI provider attribution still meaningful
    },
    "CoreWeave": {
        "cloud": "Dedicated GPU cloud provider — is itself the compute infrastructure",
        "ai":    None,
    },
    "Lambda Labs": {
        "cloud": "GPU cloud provider — is itself the compute infrastructure",
        "ai":    None,
    },
    "Lambda": {
        "cloud": "GPU cloud provider — is itself the compute infrastructure",
        "ai":    None,
    },
    "Crusoe": {
        "cloud": "Stranded-energy GPU cloud — is itself the compute infrastructure",
        "ai":    None,
    },
    "Vast.ai": {
        "cloud": "GPU compute marketplace — is itself the compute infrastructure",
        "ai":    None,
    },
    "Vultr": {
        "cloud": "Cloud infrastructure provider — is itself the compute infrastructure",
        "ai":    None,
    },
    "Fluidstack": {
        "cloud": "GPU cloud provider — is itself the compute infrastructure",
        "ai":    None,
    },
}

# Industries where physical / on-premises infrastructure is the *default* assumption.
# For these company types, weak cloud signals (founder priors, IP/ASN, HTTP headers,
# investor priors) are suppressed unless a stronger signal is found (DNS CNAME to a
# cloud provider, a partnership page, or job postings that explicitly reference cloud
# infra rather than just listing cloud tech as a candidate skill requirement).
#
# Matching is case-insensitive substring — "AI Chip Design" matches "ai chip design".
HARDWARE_INDUSTRIES: frozenset = frozenset({
    "ai chip design",
    "chip design",
    "hardware",
    "humanoid robotics",
    "robotics",
    "space infrastructure",
    "nuclear fuel",
    "nuclear energy",
    "fusion energy",
    "biotech mfg",
    "biotech manufacturing",
    "wet lab",
})

INVESTOR_CLOUD_PRIORS: dict[str, tuple[str, str]] = {
    # Google Ventures / GV — portfolio often on GCP
    "gv":                         ("GCP", "Google Ventures portfolio company"),
    "google ventures":            ("GCP", "Google Ventures portfolio company"),
    # Google itself as investor
    "google":                     ("GCP", "Google-backed company"),
    # Gradient Ventures (Google's AI fund)
    "gradient ventures":          ("GCP", "Gradient Ventures (Google AI fund) portfolio"),
    # CapitalG (Google's growth fund)
    "capitalg":                   ("GCP", "CapitalG (Google growth fund) portfolio"),
    # Microsoft / M12
    "m12":                        ("Azure", "M12 (Microsoft Ventures) portfolio company"),
    "microsoft":                  ("Azure", "Microsoft-backed company"),
    # Amazon / AWS Industrial Innovation Fund
    "amazon industrial innovation fund": ("AWS", "Amazon Industrial Innovation Fund portfolio"),
    "alexa fund":                 ("AWS", "Amazon Alexa Fund portfolio"),
    # Intel Capital
    "intel capital":              ("AWS", "Intel Capital portfolio"),  # weak — Intel is cloud-agnostic
}

# Founder background → cloud provider priors
# When founders are publicly known to be ex-employees of a cloud/AI company,
# and no stronger signal exists, we emit a WEAK (0.3) inference signal.
# These are heuristics based on typical founder loyalty and existing relationships.
FOUNDER_CLOUD_PRIORS: dict[str, tuple[str, str]] = {
    # Google — specific sub-orgs first, broad "google" fallback last
    # (substring matching means specific keys match before the broad one fires)
    "google brain":       ("GCP",   "Founded by ex-Google Brain researchers"),
    "deepmind":           ("GCP",   "Founded by ex-DeepMind researchers"),
    "google research":    ("GCP",   "Founded by ex-Google Research team"),
    "google cloud":       ("GCP",   "Founded by ex-Google Cloud team"),
    "google x":           ("GCP",   "Founded by ex-Google X team"),
    "google":             ("GCP",   "Founded by ex-Google team"),  # broad fallback — still WEAK (0.3)
    # Microsoft
    "microsoft research": ("Azure", "Founded by ex-Microsoft Research team"),
    # OpenAI → Azure (OpenAI's exclusive cloud is Azure)
    "openai":             ("Azure", "Founded by ex-OpenAI team"),
    # Amazon / AWS
    "aws":                ("AWS",   "Founded by ex-AWS team"),
    "amazon":             ("AWS",   "Founded by ex-Amazon team"),
}