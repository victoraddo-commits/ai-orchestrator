"""
Ghana Legal Source Registry — Comprehensive catalog of authoritative Ghana legal
sources. Each entry carries acquisition metadata, discovery URLs, document types,
and rights classification. Feeds the existing KLAUS acquisition pipeline.

Sources are REGISTERED but NOT necessarily ACQUIRED — each must pass the
rights gate before any documents are permanently stored.

Directive sections 12-30: all named Ghana legal sources.
"""

from __future__ import annotations

import hashlib
import logging
import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger("klaus.source_registry")

# ── Rights Gate constants ─────────────────────────────────────────────────

ACQUISITION_STATUS = {
    "PERMITTED": "eligible for full acquisition",
    "RESTRICTED": "blocked — paywalled, fee, subscription, or login-walled",
    "UNVERIFIED": "rights status not yet determined",
    "EXCLUDED": "does not meet acquisition criteria",
    "ALTERNATIVE_AVAILABLE": "same material exists from unrestricted source",
}

# ── Source Catalog ────────────────────────────────────────────────────────


@dataclass
class GhanaLegalSource:
    """One authoritative Ghana legal source."""

    # Identity
    key: str                                    # stable machine-readable slug
    name: str                                   # human-readable org name
    domain: str                                 # primary domain
    jurisdiction: str = "Ghana"
    tier: int = 1                               # KLAUS tier (1-3)

    # Acquisition metadata
    acquisition_status: str = "UNVERIFIED"      # PERMITTED | RESTRICTED | UNVERIFIED
    rights_classification: str = "pending"       # public_domain | official_public_access | etc.

    # Discovery endpoints (ordered by preference)
    base_url: str = ""
    discovery_urls: List[str] = field(default_factory=list)
    api_endpoints: List[str] = field(default_factory=list)
    oai_pmh_endpoint: str = ""
    sitemap_urls: List[str] = field(default_factory=list)
    rss_urls: List[str] = field(default_factory=list)
    dspace_endpoint: str = ""                   # e.g. /rest for DSpace REST

    # Document types expected from this source
    document_types: List[str] = field(default_factory=list)

    # Monitoring
    monitoring_frequency: str = "daily"          # high | daily | weekly
    parent_org: str = ""
    notes: str = ""


# ── GHANA LEGAL SOURCE CATALOG ────────────────────────────────────────────


GHANA_LEGAL_SOURCES: List[GhanaLegalSource] = [

    # ═══════════════════════════════════════════════════════════════════════
    # PARLIAMENT & LEGISLATION
    # ═══════════════════════════════════════════════════════════════════════

    GhanaLegalSource(
        key="parliament_main",
        name="Parliament of Ghana",
        domain="parliament.gh",
        tier=1,
        acquisition_status="PERMITTED",
        rights_classification="official_public_access",
        base_url="https://www.parliament.gh",
        discovery_urls=[
            "https://www.parliament.gh/docs",
            "https://www.parliament.gh/publications",
            "https://repository.parliament.gh/home",
            "https://erp.parliament.gh/catalog.html",
        ],
        sitemap_urls=["https://www.parliament.gh/sitemap.xml", "https://repository.parliament.gh/sitemap"],
        document_types=[
            "act", "bill", "legislative_instrument", "constitutional_instrument",
            "executive_instrument", "committee_report", "official_report",
            "hansard", "order_paper", "votes_and_proceedings",
            "standing_order", "parliamentary_agreement",
        ],
        monitoring_frequency="daily",
        notes="Primary source for enacted and proposed legislation. DSpace repository at /home.",
    ),

    # ═══════════════════════════════════════════════════════════════════════
    # JUDICIARY & COURTS
    # ═══════════════════════════════════════════════════════════════════════

    GhanaLegalSource(
        key="judicial_service",
        name="Judicial Service of Ghana",
        domain="judicial.gov.gh",
        tier=1,
        acquisition_status="RESTRICTED",
        rights_classification="official_public_access",
        base_url="https://judicial.gov.gh",
        discovery_urls=[
            "https://judicial.gov.gh/e-judgment/",
            "https://www.ejudgment.judicial.gov.gh/",
        ],
        document_types=[
            "supreme_court_judgment", "court_of_appeal_judgment",
            "high_court_judgment", "circuit_court_judgment",
            "district_court_judgment", "court_rules", "judicial_publication",
        ],
        monitoring_frequency="daily",
        notes="eJudgment portal is login-walled. Metadata discovery only. Monitor for "
              "alternative unrestricted access to judgments.",
    ),

    GhanaLegalSource(
        key="ghalii",
        name="Ghana Legal Information Institute (GhaLII)",
        domain="ghalii.org",
        tier=2,
        acquisition_status="RESTRICTED",
        rights_classification="open_license",
        base_url="https://ghalii.org",
        discovery_urls=[
            "https://ghalii.org/judgments/",
            "https://ghalii.org/legislation/",
        ],
        sitemap_urls=["https://ghalii.org/sitemap.xml"],
        document_types=[
            "supreme_court_judgment", "court_of_appeal_judgment",
            "high_court_judgment", "legislation", "constitutional_instrument",
        ],
        monitoring_frequency="daily",
        notes="Cloudflare WAF blocks automated access (403). Use for metadata/citation "
              "discovery only until AfricanLII API access is obtained. robots.txt allows "
              "search=yes. Prefer judicial.gov.gh for actual judgment acquisition.",
    ),

    # ═══════════════════════════════════════════════════════════════════════
    # GAZETTE & OFFICIAL PUBLICATION
    # ═══════════════════════════════════════════════════════════════════════

    GhanaLegalSource(
        key="ghana_publishing",
        name="Ghana Publishing Company",
        domain="ghanapublishing.gov.gh",
        tier=1,
        acquisition_status="RESTRICTED",
        rights_classification="paywalled",
        base_url="https://ghanapublishing.gov.gh",
        discovery_urls=[
            "https://ghanapublishing.gov.gh/publications/",
            "https://ghanapublishing.gov.gh/gazette/",
        ],
        document_types=[
            "gazette_issue", "act", "legislative_instrument",
            "constitutional_instrument", "executive_instrument",
            "legal_notice", "judicial_notice",
        ],
        monitoring_frequency="daily",
        notes="PAYWALLED — gpclonline.com login redirect. Metadata discovery only. "
              "Alternative: search for Gazette material on mojagd.gov.gh or "
              "parliament.gh. Do NOT acquire document content.",
    ),

    # ═══════════════════════════════════════════════════════════════════════
    # ATTORNEY-GENERAL & MINISTRY OF JUSTICE
    # ═══════════════════════════════════════════════════════════════════════

    GhanaLegalSource(
        key="mojagd",
        name="Ministry of Justice and Attorney-General's Department",
        domain="mojagd.gov.gh",
        tier=1,
        acquisition_status="UNVERIFIED",
        rights_classification="pending",
        base_url="https://mojagd.gov.gh",
        discovery_urls=[
            "https://mojagd.gov.gh/publications/",
            "https://mojagd.gov.gh/legislation/",
            "https://mojagd.gov.gh/resources/",
        ],
        sitemap_urls=["https://mojagd.gov.gh/sitemap.xml"],
        document_types=[
            "legislation", "legislative_drafting_material",
            "subsidiary_legislation", "ci", "li", "ei",
            "gazette_notice", "treaty", "convention",
            "law_reform", "official_legal_report", "legal_notice",
        ],
        monitoring_frequency="daily",
        notes="Primary government legal portal. Also discover associated institutions: "
              "Law Reform Commission, Council for Law Reporting, Legal Aid Commission, "
              "Copyright Office, Registrar of Companies.",
    ),

    # ═══════════════════════════════════════════════════════════════════════
    # COUNCIL FOR LAW REPORTING
    # ═══════════════════════════════════════════════════════════════════════

    GhanaLegalSource(
        key="council_law_reporting",
        name="Council for Law Reporting",
        domain="clr.gov.gh",
        tier=1,
        acquisition_status="RESTRICTED",
        rights_classification="commercial_licensed",
        base_url="",
        discovery_urls=[],
        document_types=[
            "law_report", "case_report", "publication_index",
        ],
        monitoring_frequency="weekly",
        notes="Ghana Law Reports are commercial products. Do NOT acquire law-report "
              "content. Use for discovery and citation verification only. Prefer "
              "underlying judgments from Judicial Service.",
    ),

    # ═══════════════════════════════════════════════════════════════════════
    # FINANCIAL REGULATORS
    # ═══════════════════════════════════════════════════════════════════════

    GhanaLegalSource(
        key="bank_of_ghana",
        name="Bank of Ghana",
        domain="bog.gov.gh",
        tier=1,
        acquisition_status="UNVERIFIED",
        rights_classification="pending",
        base_url="https://www.bog.gov.gh",
        discovery_urls=[
            "https://www.bog.gov.gh/downloads/supervision-and-regulation-downloads/",
            "https://www.bog.gov.gh/downloads/supervision-and-regulation-downloads/regulations-directives/",
        ],
        sitemap_urls=["https://www.bog.gov.gh/sitemap.xml"],
        document_types=[
            "banking_act", "regulation", "directive", "notice",
            "payment_system_rule", "foreign_exchange_rule",
            "prudential_requirement", "licensing_requirement",
            "regulatory_instrument", "exposure_draft",
        ],
        monitoring_frequency="daily",
        parent_org="Ministry of Finance",
    ),

    GhanaLegalSource(
        key="sec_ghana",
        name="Securities and Exchange Commission Ghana",
        domain="sec.gov.gh",
        tier=2,
        acquisition_status="UNVERIFIED",
        rights_classification="pending",
        base_url="https://sec.gov.gh",
        discovery_urls=[
            "https://sec.gov.gh/directivesandguidelines/",
            "https://sec.gov.gh/category/directives/",
            "https://sec.gov.gh/category/public-notice/",
        ],
        document_types=[
            "securities_act", "regulation", "rule", "directive",
            "guideline", "public_notice", "regulatory_decision",
            "amendment", "consultation_draft", "final_instrument",
        ],
        monitoring_frequency="daily",
    ),

    GhanaLegalSource(
        key="nic_ghana",
        name="National Insurance Commission",
        domain="nicgh.org",
        tier=2,
        acquisition_status="UNVERIFIED",
        rights_classification="pending",
        base_url="https://nicgh.org",
        discovery_urls=[
            "https://nicgh.org/legislation/",
            "https://nicgh.org/regulations/",
            "https://nicgh.org/directives/",
        ],
        document_types=[
            "insurance_act", "regulation", "directive", "notice",
        ],
        monitoring_frequency="weekly",
    ),

    GhanaLegalSource(
        key="fic_ghana",
        name="Financial Intelligence Centre",
        domain="fic.gov.gh",
        tier=2,
        acquisition_status="UNVERIFIED",
        rights_classification="pending",
        base_url="https://fic.gov.gh",
        discovery_urls=[
            "https://fic.gov.gh/resources/",
            "https://fic.gov.gh/legislation/",
        ],
        document_types=[
            "anti_money_laundering_act", "regulation", "directive",
            "guideline", "notice",
        ],
        monitoring_frequency="weekly",
    ),

    GhanaLegalSource(
        key="gdeposit_protection",
        name="Ghana Deposit Protection Corporation",
        domain="gdpc.gov.gh",
        tier=3,
        acquisition_status="UNVERIFIED",
        rights_classification="pending",
        base_url="",
        discovery_urls=[],
        document_types=["regulation", "directive"],
        monitoring_frequency="weekly",
    ),

    # ═══════════════════════════════════════════════════════════════════════
    # TAX & REVENUE
    # ═══════════════════════════════════════════════════════════════════════

    GhanaLegalSource(
        key="gra_ghana",
        name="Ghana Revenue Authority",
        domain="gra.gov.gh",
        tier=1,
        acquisition_status="UNVERIFIED",
        rights_classification="pending",
        base_url="https://gra.gov.gh",
        discovery_urls=[
            "https://gra.gov.gh/legislation/",
            "https://gra.gov.gh/tax-laws/",
            "https://gra.gov.gh/publications/",
        ],
        document_types=[
            "tax_act", "amendment", "tax_regulation",
            "customs_legislation", "revenue_legislation",
            "ruling", "regulatory_notice", "regulatory_instrument",
        ],
        monitoring_frequency="daily",
    ),

    # ═══════════════════════════════════════════════════════════════════════
    # CORPORATE / COMMERCIAL REGULATORS
    # ═══════════════════════════════════════════════════════════════════════

    GhanaLegalSource(
        key="orc_ghana",
        name="Office of the Registrar of Companies",
        domain="orc.gov.gh",
        tier=1,
        acquisition_status="UNVERIFIED",
        rights_classification="pending",
        base_url="https://orc.gov.gh",
        discovery_urls=[
            "https://orc.gov.gh/legislations/",
        ],
        document_types=[
            "companies_act", "companies_regulations",
            "partnerships_legislation", "insolvency_legislation",
            "corporate_regulation", "amendment", "legal_notice",
            "corporate_legal_instrument",
        ],
        monitoring_frequency="weekly",
    ),

    GhanaLegalSource(
        key="gipc_ghana",
        name="Ghana Investment Promotion Centre",
        domain="gipc.gov.gh",
        tier=3,
        acquisition_status="UNVERIFIED",
        rights_classification="pending",
        base_url="https://gipc.gov.gh",
        discovery_urls=[
            "https://gipc.gov.gh/legislation/",
            "https://gipc.gov.gh/investment-laws/",
        ],
        document_types=["investment_act", "regulation", "guideline"],
        monitoring_frequency="weekly",
    ),

    GhanaLegalSource(
        key="ppa_ghana",
        name="Public Procurement Authority",
        domain="ppa.gov.gh",
        tier=3,
        acquisition_status="UNVERIFIED",
        rights_classification="pending",
        base_url="https://ppa.gov.gh",
        discovery_urls=[
            "https://ppa.gov.gh/legislation/",
            "https://ppa.gov.gh/legal-framework/",
        ],
        document_types=["procurement_act", "regulation", "guideline", "directive"],
        monitoring_frequency="weekly",
    ),

    # ═══════════════════════════════════════════════════════════════════════
    # TECHNOLOGY REGULATORS
    # ═══════════════════════════════════════════════════════════════════════

    GhanaLegalSource(
        key="nca_ghana",
        name="National Communications Authority",
        domain="nca.org.gh",
        tier=2,
        acquisition_status="UNVERIFIED",
        rights_classification="pending",
        base_url="https://nca.org.gh",
        discovery_urls=[
            "https://nca.org.gh/legislation/",
            "https://nca.org.gh/regulations/",
        ],
        document_types=[
            "electronic_communications_act", "telecommunications_regulation",
            "licensing_rule", "spectrum_regulation", "directive",
            "numbering_rule", "qos_regulation", "regulatory_notice",
        ],
        monitoring_frequency="daily",
    ),

    GhanaLegalSource(
        key="dpc_ghana",
        name="Data Protection Commission",
        domain="dataprotection.org.gh",
        tier=2,
        acquisition_status="UNVERIFIED",
        rights_classification="pending",
        base_url="https://dataprotection.org.gh",
        discovery_urls=[
            "https://dataprotection.org.gh/documents/",
        ],
        document_types=[
            "data_protection_act", "regulation", "directive",
            "compliance_instrument", "regulatory_decision", "notice",
        ],
        monitoring_frequency="daily",
    ),

    GhanaLegalSource(
        key="csa_ghana",
        name="Cyber Security Authority",
        domain="csa.gov.gh",
        tier=2,
        acquisition_status="UNVERIFIED",
        rights_classification="pending",
        base_url="https://csa.gov.gh",
        discovery_urls=[
            "https://csa.gov.gh/legislation/",
            "https://csa.gov.gh/regulations/",
        ],
        document_types=["cybersecurity_act", "regulation", "directive", "guideline"],
        monitoring_frequency="weekly",
    ),

    GhanaLegalSource(
        key="nita_ghana",
        name="National Information Technology Agency",
        domain="nita.gov.gh",
        tier=3,
        acquisition_status="UNVERIFIED",
        rights_classification="pending",
        base_url="https://nita.gov.gh",
        discovery_urls=[
            "https://nita.gov.gh/legislation/",
            "https://nita.gov.gh/regulations/",
        ],
        document_types=["it_act", "regulation", "directive", "standard"],
        monitoring_frequency="weekly",
    ),

    # ═══════════════════════════════════════════════════════════════════════
    # ENERGY REGULATORS
    # ═══════════════════════════════════════════════════════════════════════

    GhanaLegalSource(
        key="energy_ministry",
        name="Ministry of Energy and Green Transition",
        domain="energymin.gov.gh",
        tier=1,
        acquisition_status="UNVERIFIED",
        rights_classification="pending",
        base_url="https://www.energymin.gov.gh",
        discovery_urls=[
            "https://www.energymin.gov.gh/laws-regulations-and-enabling-acts",
            "https://www.energymin.gov.gh/downloads",
        ],
        document_types=[
            "energy_act", "regulation", "li", "petroleum_legislation",
            "renewable_energy_legislation", "electricity_regulation",
        ],
        monitoring_frequency="daily",
    ),

    GhanaLegalSource(
        key="energy_commission",
        name="Energy Commission",
        domain="energycom.gov.gh",
        tier=2,
        acquisition_status="UNVERIFIED",
        rights_classification="pending",
        base_url="https://energycom.gov.gh",
        discovery_urls=[
            "https://energycom.gov.gh/regulations/",
            "https://energycom.gov.gh/legal-framework/",
        ],
        document_types=["energy_act", "regulation", "rule", "licensing_requirement"],
        monitoring_frequency="weekly",
        parent_org="Ministry of Energy",
    ),

    GhanaLegalSource(
        key="purc_ghana",
        name="Public Utilities Regulatory Commission",
        domain="purc.gov.gh",
        tier=2,
        acquisition_status="UNVERIFIED",
        rights_classification="pending",
        base_url="https://www.purc.gov.gh",
        discovery_urls=[
            "https://www.purc.gov.gh/regulations/",
            "https://www.purc.gov.gh/legal-framework/",
        ],
        document_types=["utilities_regulation", "tariff_order", "directive", "rule"],
        monitoring_frequency="weekly",
    ),

    GhanaLegalSource(
        key="npa_ghana",
        name="National Petroleum Authority",
        domain="npa.gov.gh",
        tier=2,
        acquisition_status="UNVERIFIED",
        rights_classification="pending",
        base_url="https://npa.gov.gh",
        discovery_urls=[
            "https://npa.gov.gh/regulations/",
            "https://npa.gov.gh/legal-framework/",
        ],
        document_types=["petroleum_act", "regulation", "directive", "rule"],
        monitoring_frequency="weekly",
    ),

    GhanaLegalSource(
        key="petroleum_commission",
        name="Petroleum Commission",
        domain="petrocom.gov.gh",
        tier=2,
        acquisition_status="UNVERIFIED",
        rights_classification="pending",
        base_url="https://www.petrocom.gov.gh",
        discovery_urls=[
            "https://www.petrocom.gov.gh/regulations/",
            "https://www.petrocom.gov.gh/legal-framework/",
        ],
        document_types=["petroleum_act", "regulation", "directive"],
        monitoring_frequency="weekly",
    ),

    # ═══════════════════════════════════════════════════════════════════════
    # NATURAL RESOURCES
    # ═══════════════════════════════════════════════════════════════════════

    GhanaLegalSource(
        key="mlnr_ghana",
        name="Ministry of Lands and Natural Resources",
        domain="mlnr.gov.gh",
        tier=1,
        acquisition_status="UNVERIFIED",
        rights_classification="pending",
        base_url="https://mlnr.gov.gh",
        discovery_urls=[
            "https://mlnr.gov.gh/resources/",
            "https://mlnr.gov.gh/resources/legislations/",
        ],
        document_types=[
            "land_act", "land_regulation", "statutory_instrument",
            "legal_notice", "resource_legislation",
        ],
        monitoring_frequency="daily",
    ),

    GhanaLegalSource(
        key="lands_commission",
        name="Lands Commission",
        domain="lc.gov.gh",
        tier=2,
        acquisition_status="UNVERIFIED",
        rights_classification="pending",
        base_url="https://lc.gov.gh",
        discovery_urls=[
            "https://lc.gov.gh/legislation/",
            "https://lc.gov.gh/regulations/",
        ],
        document_types=["land_act", "regulation", "instrument", "notice"],
        monitoring_frequency="weekly",
    ),

    GhanaLegalSource(
        key="minerals_commission",
        name="Minerals Commission",
        domain="mincom.gov.gh",
        tier=2,
        acquisition_status="UNVERIFIED",
        rights_classification="pending",
        base_url="https://www.mincom.gov.gh",
        discovery_urls=[
            "https://www.mincom.gov.gh/regulations/",
            "https://www.mincom.gov.gh/legislation/",
        ],
        document_types=["minerals_act", "mining_regulation", "instrument"],
        monitoring_frequency="weekly",
    ),

    GhanaLegalSource(
        key="forestry_commission",
        name="Forestry Commission",
        domain="fcghana.org",
        tier=3,
        acquisition_status="UNVERIFIED",
        rights_classification="pending",
        base_url="https://fcghana.org",
        discovery_urls=[
            "https://fcghana.org/legislation/",
            "https://fcghana.org/regulations/",
        ],
        document_types=["forestry_act", "regulation", "instrument"],
        monitoring_frequency="weekly",
    ),

    GhanaLegalSource(
        key="epa_ghana",
        name="Environmental Protection Agency",
        domain="epa.gov.gh",
        tier=2,
        acquisition_status="UNVERIFIED",
        rights_classification="pending",
        base_url="https://www.epa.gov.gh",
        discovery_urls=[
            "https://www.epa.gov.gh/legislation/",
            "https://www.epa.gov.gh/regulations/",
            "https://www.epa.gov.gh/environmental-quality-standards/",
        ],
        document_types=["environmental_act", "regulation", "li", "standard", "notice"],
        monitoring_frequency="weekly",
    ),

    GhanaLegalSource(
        key="water_resources_commission",
        name="Water Resources Commission",
        domain="wrc.gov.gh",
        tier=3,
        acquisition_status="UNVERIFIED",
        rights_classification="pending",
        base_url="https://wrc.gov.gh",
        discovery_urls=[
            "https://wrc.gov.gh/legislation/",
            "https://wrc.gov.gh/regulations/",
        ],
        document_types=["water_act", "regulation", "instrument"],
        monitoring_frequency="weekly",
    ),

    # ═══════════════════════════════════════════════════════════════════════
    # IMMIGRATION
    # ═══════════════════════════════════════════════════════════════════════

    GhanaLegalSource(
        key="gis_ghana",
        name="Ghana Immigration Service",
        domain="gis.gov.gh",
        tier=2,
        acquisition_status="UNVERIFIED",
        rights_classification="pending",
        base_url="https://gis.gov.gh",
        discovery_urls=[
            "https://gis.gov.gh/gis-laws-regulations/",
        ],
        document_types=[
            "immigration_act", "immigration_regulation",
            "amendment", "migration_legislation", "regulatory_material",
        ],
        monitoring_frequency="daily",
    ),

    # ═══════════════════════════════════════════════════════════════════════
    # MINISTRY OF FINANCE
    # ═══════════════════════════════════════════════════════════════════════

    GhanaLegalSource(
        key="mofep_ghana",
        name="Ministry of Finance",
        domain="mofep.gov.gh",
        tier=1,
        acquisition_status="UNVERIFIED",
        rights_classification="pending",
        base_url="https://mofep.gov.gh",
        discovery_urls=[
            "https://mofep.gov.gh/publications/acts-and-policies",
            "https://www.mofep.gov.gh/publications/budget-statements",
        ],
        document_types=[
            "appropriation_act", "pfm_legislation", "pfm_regulation",
            "fiscal_legislation", "public_finance_instrument",
        ],
        monitoring_frequency="daily",
        notes="Budget statements, speeches, fiscal reviews are NOT legal instruments "
              "and should not be acquired unless classified as legal material by the "
              "existing Legal Module taxonomy.",
    ),

    # ═══════════════════════════════════════════════════════════════════════
    # HEALTH REGULATORS
    # ═══════════════════════════════════════════════════════════════════════

    GhanaLegalSource(
        key="fda_ghana",
        name="Food and Drugs Authority",
        domain="fdaghana.gov.gh",
        tier=2,
        acquisition_status="UNVERIFIED",
        rights_classification="pending",
        base_url="https://fdaghana.gov.gh",
        discovery_urls=[
            "https://fdaghana.gov.gh/legislation/",
            "https://fdaghana.gov.gh/regulations/",
        ],
        document_types=["food_drugs_act", "regulation", "guideline", "directive"],
        monitoring_frequency="weekly",
    ),

    GhanaLegalSource(
        key="hefra_ghana",
        name="Health Facilities Regulatory Agency",
        domain="hefra.gov.gh",
        tier=3,
        acquisition_status="UNVERIFIED",
        rights_classification="pending",
        base_url="https://hefra.gov.gh",
        discovery_urls=[
            "https://hefra.gov.gh/legislation/",
        ],
        document_types=["health_act", "regulation", "standard"],
        monitoring_frequency="weekly",
    ),

    # ═══════════════════════════════════════════════════════════════════════
    # LABOUR & SOCIAL
    # ═══════════════════════════════════════════════════════════════════════

    GhanaLegalSource(
        key="labour_commission",
        name="National Labour Commission",
        domain="nlc.gov.gh",
        tier=3,
        acquisition_status="UNVERIFIED",
        rights_classification="pending",
        base_url="https://nlc.gov.gh",
        discovery_urls=[
            "https://nlc.gov.gh/legislation/",
            "https://nlc.gov.gh/regulations/",
        ],
        document_types=["labour_act", "regulation", "instrument", "directive"],
        monitoring_frequency="weekly",
    ),

    GhanaLegalSource(
        key="ssnit_ghana",
        name="Social Security and National Insurance Trust",
        domain="ssnit.org.gh",
        tier=2,
        acquisition_status="UNVERIFIED",
        rights_classification="pending",
        base_url="https://www.ssnit.org.gh",
        discovery_urls=[
            "https://www.ssnit.org.gh/legislation/",
            "https://www.ssnit.org.gh/regulations/",
        ],
        document_types=["ssnit_act", "regulation", "instrument", "directive"],
        monitoring_frequency="weekly",
    ),

    GhanaLegalSource(
        key="npra_ghana",
        name="National Pensions Regulatory Authority",
        domain="npra.gov.gh",
        tier=2,
        acquisition_status="UNVERIFIED",
        rights_classification="pending",
        base_url="https://npra.gov.gh",
        discovery_urls=[
            "https://npra.gov.gh/legislation/",
            "https://npra.gov.gh/regulations/",
        ],
        document_types=["pensions_act", "regulation", "directive", "guideline"],
        monitoring_frequency="weekly",
    ),

    # ═══════════════════════════════════════════════════════════════════════
    # TRANSPORT REGULATORS
    # ═══════════════════════════════════════════════════════════════════════

    GhanaLegalSource(
        key="dvla_ghana",
        name="Driver and Vehicle Licensing Authority",
        domain="dvla.gov.gh",
        tier=3,
        acquisition_status="UNVERIFIED",
        rights_classification="pending",
        base_url="https://dvla.gov.gh",
        discovery_urls=[
            "https://dvla.gov.gh/legislation/",
        ],
        document_types=["road_traffic_act", "regulation", "instrument"],
        monitoring_frequency="weekly",
    ),

    GhanaLegalSource(
        key="maritime_authority",
        name="Ghana Maritime Authority",
        domain="ghanamaritimeauthority.com",
        tier=3,
        acquisition_status="UNVERIFIED",
        rights_classification="pending",
        base_url="https://www.ghanamaritimeauthority.com",
        discovery_urls=[
            "https://www.ghanamaritimeauthority.com/legislation/",
            "https://www.ghanamaritimeauthority.com/regulations/",
        ],
        document_types=["maritime_act", "regulation", "instrument"],
        monitoring_frequency="weekly",
    ),

    GhanaLegalSource(
        key="gcaa_ghana",
        name="Ghana Civil Aviation Authority",
        domain="gcaa.com.gh",
        tier=3,
        acquisition_status="UNVERIFIED",
        rights_classification="pending",
        base_url="https://www.gcaa.com.gh",
        discovery_urls=[
            "https://www.gcaa.com.gh/legislation/",
            "https://www.gcaa.com.gh/regulations/",
        ],
        document_types=["aviation_act", "regulation", "instrument", "directive"],
        monitoring_frequency="weekly",
    ),

    # ═══════════════════════════════════════════════════════════════════════
    # JUSTICE / ACCOUNTABILITY
    # ═══════════════════════════════════════════════════════════════════════

    GhanaLegalSource(
        key="chraj_ghana",
        name="Commission on Human Rights and Administrative Justice",
        domain="chraj.gov.gh",
        tier=2,
        acquisition_status="UNVERIFIED",
        rights_classification="pending",
        base_url="https://chraj.gov.gh",
        discovery_urls=[
            "https://chraj.gov.gh/legislation/",
            "https://chraj.gov.gh/publications/",
        ],
        document_types=["human_rights_act", "regulation", "report"],
        monitoring_frequency="weekly",
    ),

    GhanaLegalSource(
        key="eoco_ghana",
        name="Economic and Organised Crime Office",
        domain="eoco.gov.gh",
        tier=3,
        acquisition_status="UNVERIFIED",
        rights_classification="pending",
        base_url="https://eoco.gov.gh",
        discovery_urls=[
            "https://eoco.gov.gh/legislation/",
        ],
        document_types=["eoco_act", "regulation"],
        monitoring_frequency="weekly",
    ),

    GhanaLegalSource(
        key="electoral_commission",
        name="Electoral Commission of Ghana",
        domain="ec.gov.gh",
        tier=2,
        acquisition_status="UNVERIFIED",
        rights_classification="pending",
        base_url="https://ec.gov.gh",
        discovery_urls=[
            "https://ec.gov.gh/legislation/",
            "https://ec.gov.gh/constitutional-instruments/",
        ],
        document_types=["electoral_act", "ci", "regulation", "instrument"],
        monitoring_frequency="weekly",
    ),

    # ═══════════════════════════════════════════════════════════════════════
    # CONSTITUTIONAL BODIES
    # ═══════════════════════════════════════════════════════════════════════

    GhanaLegalSource(
        key="constituteproject",
        name="Constitute Project",
        domain="constituteproject.org",
        tier=2,
        acquisition_status="PERMITTED",
        rights_classification="public_domain",
        base_url="https://www.constituteproject.org",
        discovery_urls=[
            "https://www.constituteproject.org/constitution/Ghana_1992",
        ],
        document_types=["constitution"],
        monitoring_frequency="weekly",
        notes="Constitution text is public domain. Only acquire the Ghana Constitution. "
              "Do NOT acquire comparative analysis or editorial content from other "
              "jurisdictions.",
    ),
]


# ── Source lookup helpers ──────────────────────────────────────────────────

def get_source(key: str) -> Optional[GhanaLegalSource]:
    """Get a single source by its key."""
    for s in GHANA_LEGAL_SOURCES:
        if s.key == key:
            return s
    return None


def get_permitted_sources() -> List[GhanaLegalSource]:
    """Return only sources with PERMITTED acquisition status."""
    return [s for s in GHANA_LEGAL_SOURCES if s.acquisition_status == "PERMITTED"]


def get_unverified_sources() -> List[GhanaLegalSource]:
    """Return sources that need rights verification."""
    return [s for s in GHANA_LEGAL_SOURCES if s.acquisition_status == "UNVERIFIED"]


def get_restricted_sources() -> List[GhanaLegalSource]:
    """Return restricted sources (metadata discovery only)."""
    return [s for s in GHANA_LEGAL_SOURCES if s.acquisition_status == "RESTRICTED"]


def get_sources_by_tier(tier: int) -> List[GhanaLegalSource]:
    """Return sources at a specific KLAUS tier."""
    return [s for s in GHANA_LEGAL_SOURCES if s.tier == tier]


def get_sources_by_frequency(frequency: str) -> List[GhanaLegalSource]:
    """Return sources with a specific monitoring frequency."""
    return [s for s in GHANA_LEGAL_SOURCES if s.monitoring_frequency == frequency]


# ── Government domain auto-discovery ──────────────────────────────────────

GOVERNMENT_DISCOVERY_PATTERNS = [
    # Common paths where legal documents live on gov.gh sites
    "/legislation", "/legislations", "/laws", "/law",
    "/acts", "/bills", "/regulations", "/rules",
    "/directives", "/guidelines", "/notices",
    "/judgments", "/judgements", "/decisions", "/cases",
    "/court", "/gazette",
    "/documents", "/downloads", "/publications", "/resources",
    "/instruments", "/legal-framework", "/legal",
]

GHANA_GOV_DOMAINS = [
    # .gov.gh — official government
    "gov.gh",
    # .org.gh — some statutory bodies
    "org.gh",
    # .com.gh — some regulatory bodies
    "com.gh",
]

KNOWN_NON_LEGAL_DOMAINS = {
    # Domains that are gov.gh but unlikely to host legal material
    "ghana.gov.gh",        # portal — no document archive
    "presidency.gov.gh",   # presidential communications only
    "mfa.gov.gh",          # foreign affairs — no domestic legal archive
}


def is_ghana_government_domain(domain: str) -> bool:
    """Check if a domain is a Ghana government domain."""
    domain = domain.lower()
    if domain in KNOWN_NON_LEGAL_DOMAINS:
        return False
    return any(domain.endswith(f".{d}") for d in GHANA_GOV_DOMAINS)


def generate_discovery_urls(base_url: str) -> List[str]:
    """Generate potential legal discovery URLs for a government domain."""
    urls = []
    base = base_url.rstrip("/")
    for pattern in GOVERNMENT_DISCOVERY_PATTERNS:
        urls.append(f"{base}{pattern}")
    return urls


# ── Source registration ───────────────────────────────────────────────────

def register_sources_in_db() -> Dict[str, int]:
    """Register all Ghana legal sources in the KLAUS database.

    Idempotent — skips already-registered URLs.
    Returns counts: {inserted, skipped, failed}.
    """
    from core.klaus.db_manager import add_source, list_sources

    existing = list_sources(status=None)
    existing_urls = {s["url"] for s in existing} if existing else set()

    counts = {"inserted": 0, "skipped": 0, "failed": 0}

    for src in GHANA_LEGAL_SOURCES:
        # Use the first discovery URL as the primary, or base_url
        primary_url = src.base_url or (src.discovery_urls[0] if src.discovery_urls else "")

        if not primary_url:
            counts["skipped"] += 1
            continue

        if primary_url in existing_urls:
            counts["skipped"] += 1
            continue

        # Only register PERMITTED or UNVERIFIED sources for actual acquisition.
        # RESTRICTED sources are registered for metadata/provenance tracking.
        try:
            add_source(
                url=primary_url,
                domain=src.domain,
                tier=src.tier,
                jurisdiction=src.jurisdiction,
            )
            counts["inserted"] += 1
        except Exception as e:
            logger.warning(f"Failed to register source {src.key}: {e}")
            counts["failed"] += 1

    return counts


def print_catalog_summary() -> str:
    """Return a human-readable catalog summary."""
    permitted = len(get_permitted_sources())
    restricted = len(get_restricted_sources())
    unverified = len(get_unverified_sources())
    total = len(GHANA_LEGAL_SOURCES)

    lines = [
        f"Ghana Legal Source Catalog: {total} sources",
        f"  PERMITTED:  {permitted}",
        f"  RESTRICTED: {restricted}",
        f"  UNVERIFIED: {unverified}",
        f"  Tier 1: {len(get_sources_by_tier(1))}",
        f"  Tier 2: {len(get_sources_by_tier(2))}",
        f"  Tier 3: {len(get_sources_by_tier(3))}",
        "",
        "Tier 1 (core legislation):",
    ]
    for s in get_sources_by_tier(1):
        lines.append(f"  {s.key:30s} — {s.name} [{s.acquisition_status}]")
    lines.append("\nTier 2 (major regulators):")
    for s in get_sources_by_tier(2):
        lines.append(f"  {s.key:30s} — {s.name} [{s.acquisition_status}]")
    lines.append("\nTier 3 (other authorities):")
    for s in get_sources_by_tier(3):
        lines.append(f"  {s.key:30s} — {s.name} [{s.acquisition_status}]")

    return "\n".join(lines)
