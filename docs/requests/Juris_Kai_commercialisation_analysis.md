# Juris Kai — Commercialisation & Legal-Structure Analysis

Prepared 2026-09-24. **Not legal advice** — engage a Ghanaian corporate/IP lawyer before acting.

## 1. The core question: "non-profit but still makes money"?

**A non-profit is not forbidden from earning money.** The distinction is *distribution*, not *income*:

| | Non-profit | For-profit |
|---|---|---|
| May earn revenue (fees, subscriptions, sales)? | **Yes** | Yes |
| May distribute surplus/profit to owners/members? | **No** | Yes |
| Surplus must be | re-invested in the mission | at owners' discretion |

So "non-profit that charges fees" is lawful and common. **But** you cannot be a non-profit *and* personally take the profits — claiming non-profit status while distributing surplus is misrepresentation (and a tax/regulatory breach).

## 2. Three lawful structures

**A. Mission-only non-profit** — company limited by guarantee (Companies Act 2019, Act 992) / trust / society; may charge fees; all surplus reinvested; eligible for grants and goodwill; **no personal profit**.

**B. Hybrid (recommended for Juris Kai)** — a **non-profit foundation** + a **for-profit company**:
- Foundation: holds the **free/public-good** layer — the statute & constitution corpus, free tier, legal-education content; can receive grants and (where permitted) licensed/government data.
- Company: runs the **commercial** layer — premium subscriptions, API, enterprise tools.
- An **arm's-length licence/service agreement** links them (foundation licenses its own corpus to the company; company funds the mission).
- ⚠️ **Critical rule:** content licensed **Non-Commercial** (e.g. anything from GhaLII) or **permission-required** (Laws.Africa) must **never** flow into the commercial arm — passing it there *is* commercial use and breaches the licence.

**C. Commercial company with a free public tier** — simplest; no non-profit status; build goodwill via a free tier.

## 3. What can be commercialised (legality map)

| Asset | Copyrightable? | Can sell? |
|---|---|---|
| **Ghana enactments** (Acts, LIs, CIs, EIs) | **No** (Copyright Act 2005 s.8(1)(a)) | **Likely yes** — confirm host/DSpace terms |
| **Court decisions** (raw text) | **No** (s.8(1)(b)) | Yes *if* lawfully obtained; host terms apply |
| **Headnotes / editorial / summaries** | **Yes** (protected) | **No** (not ours) |
| **Our software** (retrieval, KG, reasoning, agent) | Yes (our IP) | **Yes — fully** |
| **Our value-added output** (analysis, drafting, tools) | Yes | **Yes** |
| **GhaLII content** (CC BY-NC) | Mixed | **No — Non-Commercial** |
| **Laws.Africa content** | Mixed | **No — permission required** |
| **User data** | n/a | **Heavily restricted** (Data Protection Act 2012, Act 843) |

**Bottom line:** the **software, the tools, and the value-added services are ours to sell freely**. The **law text** is generally sellable because it isn't copyrightable — *provided* the host's access terms allow it. Editorial layers and NC/permission sources are off-limits.

## 4. Ways to make money (recommended mix)

1. **Freemium SaaS** — free statute lookup tier; paid tiers for Deep Research, contract analysis, API, priority (Paystack already integrated).
2. **Subscriptions** — consumer (students, small firms) + professional (lawyers) tiers.
3. **API / usage-based** — charge developers and firms per call/volume.
4. **Enterprise & institutional licences** — law firms, banks, corporates, universities, government agencies (annual seat/usage licences).
5. **Professional services** — bespoke research, contract review, compliance, training/CPD.
6. **Grants & donations** — for the **non-profit foundation** (public-good legal-information mission).
7. **Whitelanding / partnerships** — bar associations, law schools, regulator portals.

## 5. "No issues" legal checklist

- [ ] **Entity**: choose A/B/C; register properly (ORC / Social Welfare / Registrar-General). Run the right business through the right entity.
- [ ] **Copyright**: only copyright-excluded law + our own work; never editorial or NC/permission content.
- [ ] **Source-licence register**: every corpus source tagged with commercial-use permission; the system must **refuse to serve** content whose licence forbids commercial use. ← *build this*
- [ ] **Data Protection Act 2012 (Act 843)**: register with the Data Protection Commission; lawful basis; redact personal data in judgments; user-data policy; security.
- [ ] **Unauthorised practice of law** (Legal Profession Act 1960, Act 32): keep it explicitly a *research/information tool*, not legal advice; clear disclaimers; no lawyer-client relationship.
- [ ] **Tax**: income tax on commercial revenue; check any non-profit exemptions; VAT thresholds.
- [ ] **Payments**: Payment Systems and Services Act 2019 (Act 987) compliance via Paystack/Sika.
- [ ] **Consumer protection & contracts**: Terms of Service, Privacy Policy, Refund Policy, SLA.
- [ ] **Licences**: get written licences from Judicial Service / Council for Law Reporting **that expressly permit commercial + AI use** (letters drafted).
- [ ] **Professional indemnity**: consider insurance for a paid legal-research product.

## 6. Recommendation

Adopt **Structure B (hybrid)**:
- **Non-profit foundation** = the free public-good law corpus & education mission (grants, goodwill, credibility).
- **Commercial company** = the paid software/tools/enterprise, licensing only commercially-safe content from the foundation.
- Keep **all NC/permission/editorial content out of the commercial arm**; enforce via a source-licence register + a runtime commercial-use gate.

Then build the **source-licence register + commercial-use gate** so the system is technically incapable of selling restricted content.
