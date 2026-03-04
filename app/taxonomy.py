"""
Vertical / Sub-Vertical / Cloud Propensity Taxonomy

Single source of truth for company classification. Used by:
  - app/classification/classifier.py  (LLM prompt + validation)
  - pipeline.py                       (propensity lookup after classification)
  - dashboard filters                 (vertical + propensity dropdown options)

Propensity is a property of the sub-vertical, not the individual company.
Once a company is classified, its propensity tag is looked up here.

To add a new sub-vertical: add it to the appropriate vertical below,
then re-run backfill for any companies that might match.
"""

TAXONOMY: dict[str, dict] = {
    "AI Infrastructure & Compute": {
        "propensity": "High",
        "sub_verticals": {
            "Foundational model builders":                      "High",
            "AI Hardware Accelerators / Specialized Compute":   "Medium",
            "MLOps / Training platforms":                       "High",
            "Vector databases":                                 "High",
        },
    },
    "AI Applications & Tooling": {
        "propensity": "High",
        "sub_verticals": {
            "AI Development Tools & Agent frameworks":          "High",
            "Vertical-specific AI apps":                        "High",
        },
    },
    "B2B SaaS / Enterprise": {
        "propensity": "High",
        "sub_verticals": {
            "API-first services":                               "High",
            "Data-analytics platforms":                         "High",
            "CRM":                                              "Medium",
            "Collaboration / comms":                            "Medium",
            "Project-management tools":                         "Medium",
            "Endpoint security":                                "Medium",
        },
    },
    "Climate & Energy Tech": {
        "propensity": "Medium",
        "sub_verticals": {
            "Grid-management software":                         "High",
            "Carbon-capture process compute":                   "High",
            "Carbon-accounting SaaS":                           "High",
            "EV-charging infra SW":                             "Medium",
            "Precision-agriTech":                               "Medium",
            "Sustainable-materials R&D":                        "Medium",
            "Fusion & advanced nuclear":                        "Medium",
            "Geothermal & subsurface looping":                  "Low",
            "Utility-scale solar & wind EPC":                   "Low",
            "Next-gen battery tech & Li supply-chain":          "Low",
        },
    },
    "Consumer / E-commerce & Marketplaces": {
        "propensity": "High",
        "sub_verticals": {
            "Travel / leisure marketplaces":                    "High",
            "E-commerce enablers":                              "High",
            "Social media & consumer apps":                     "High",
            "Streaming & Immersive Media":                      "High",
            "Connected Consumer Hardware & IoT":                "Medium",
            "DTC infra-platforms":                              "Medium",
        },
    },
    "Cybersecurity": {
        "propensity": "High",
        "sub_verticals": {
            "Data-security / privacy / vaulting":               "High",
            "CSPM / CNAPP":                                     "High",
            "Threat Detection & Response / SIEM":               "High",
            "IAM & auth":                                       "High",
            "Endpoint security":                                "Medium",
        },
    },
    "Data Infrastructure": {
        "propensity": "High",
        "sub_verticals": {
            "Data pipelines & ETL":                             "High",
            "Lakehouse & data warehouse":                       "High",
            "Reverse ETL & data activation":                    "High",
            "Data quality & observability":                     "High",
        },
    },
    "Developer Tools": {
        "propensity": "High",
        "sub_verticals": {
            "CI/CD & DevOps platforms":                         "High",
            "Observability & monitoring":                       "High",
            "API management & gateways":                        "High",
            "Security tooling for developers":                  "High",
        },
    },
    "Education Tech": {
        "propensity": "Medium",
        "sub_verticals": {
            "AI tutoring & adaptive learning":                  "High",
            "Learning management platforms":                    "Medium",
            "Credentialing & skills platforms":                  "Medium",
        },
    },
    "Fintech, Payments and Crypto": {
        "propensity": "High",
        "sub_verticals": {
            "LendingTech":                                      "High",
            "Investment Tech":                                  "High",
            "Digital Banking / Neobanks":                       "High",
            "Payment Processing":                               "High",
            "RegTech & compliance":                             "High",
            "InsurTech":                                        "High",
            "Crypto / Web3 infra":                              "Low",
            "DeFi":                                             "Low",
        },
    },
    "Healthcare, BioTech & Life Sciences": {
        "propensity": "High",
        "sub_verticals": {
            "AI drug discovery":                                "High",
            "Health-data analytics":                            "High",
            "Genomics & bioinformatics":                        "High",
            "Medical-device IoT platforms":                     "High",
            "Tele-health platforms":                            "High",
            "Digital therapeutics (DTx)":                        "Medium",
            "Cell & gene therapy manufacturing":                "Medium",
            "Bio-foundries & wet-lab platforms":                "Medium",
            "Oncology & cancer immunotherapy":                  "Medium",
        },
    },
    "HR Tech / Workforce Tech": {
        "propensity": "High",
        "sub_verticals": {
            "Recruiting & talent intelligence":                 "High",
            "Workforce analytics":                              "High",
            "Skills & learning platforms":                       "Medium",
            "Payroll & benefits platforms":                      "Medium",
        },
    },
    "Industrial / IoT / Robotics": {
        "propensity": "High",
        "sub_verticals": {
            "Robotics control platforms":                       "Medium",
            "Warehouse / logistics platforms":                  "High",
            "Industrial IoT platforms":                         "High",
            "Process-optimisation SW":                          "High",
            "Digital twins":                                    "High",
            "Predictive maintenance":                           "High",
        },
    },
    "Legal Tech": {
        "propensity": "High",
        "sub_verticals": {
            "Contract analysis & management":                   "High",
            "E-discovery & litigation support":                 "High",
            "Regulatory compliance automation":                 "High",
        },
    },
    "Aero / Defence / Space": {
        "propensity": "Medium",
        "sub_verticals": {
            "Sat-image analytics":                              "High",
            "Autonomous systems":                               "Medium",
            "Mobility & Transportation Tech":                   "Medium",
            "Launch-systems software":                          "Low",
        },
    },
    "PropTech / Real Estate Tech": {
        "propensity": "Medium",
        "sub_verticals": {
            "Property data & valuation platforms":              "High",
            "Smart building & IoT platforms":                   "Medium",
            "Real estate transaction platforms":                "Medium",
        },
    },
    "Construction Tech / AEC": {
        "propensity": "High",
        "sub_verticals": {
            "AI project management & scheduling":               "High",
            "BIM & digital design platforms":                   "High",
            "Safety monitoring & site IoT":                     "Medium",
        },
    },
}

# ---------------------------------------------------------------------------
# Derived lookups — all computed from TAXONOMY, never hardcoded separately.
# ---------------------------------------------------------------------------

# Flat lookup: sub_vertical -> propensity
SUB_VERTICAL_PROPENSITY: dict[str, str] = {}
for _vdata in TAXONOMY.values():
    for _sv, _prop in _vdata["sub_verticals"].items():
        SUB_VERTICAL_PROPENSITY[_sv] = _prop

# Valid values for validation
VALID_VERTICALS = list(TAXONOMY.keys())
VALID_SUB_VERTICALS = list(SUB_VERTICAL_PROPENSITY.keys())
VALID_PROPENSITY = ["High", "Medium", "Low"]
