# Legal Brain 2.0 — Phase 5 Task 4: Quick vs Deep live validation

Generated: 2026-09-24T09:32:46+00:00

Model: local VM104 `qwen3-coder:kai`  ·  Corpus: CT100 (live)
Quick = single grounded pass  ·  Deep = retrieve → advocate → oppose → judge

| Question | Quick verdict | Quick lat (s) | Deep est/disp/unres | Deep auths | Deep contrary | Deep lat (s) | Degraded |
|----------|---------------|--------------:|:-------------------:|-----------:|--------------:|-------------:|:--------:|
| rape | GROUNDED | 3.07 | 3/2/11 | 3 | 0 | 33.05 | no |
| human_rights | GROUNDED | 6.5 | 8/4/4 | 6 | 3 | 36.2 | no |
| civil_procedure | GROUNDED | 4.0 | 4/6/5 | 6 | 3 | 34.19 | no |
| bill | GROUNDED | 5.1 | 3/5/7 | 4 | 1 | 38.4 | no |
| absurd_vacuous | UNGROUNDED | 0.0 | refused | 0 | 0 | 0.0 | - |
| absurd_nonsense | PARTIAL | 5.84 | 2/2/12 | 11 | 9 | 35.06 | no |

## Per-question detail

### rape — What is the offence of rape under Ghanaian law?
- verdict: **GROUNDED** · retrieved sources: 3 · footer tags: ['AMENDED', 'PROPOSED']
- Quick (3.07s): The sources provided do not explicitly define the offence of rape under Ghanaian law. While **Source 1 (Criminal Offences Act, 1960, Act 29)** outlines general provisions and structures for criminal offences, it does not contain a specific definition of rape.…
- Deep (33.05s; retrieve None · advocate 8.21 · opponent 15.157 · judge 9.681): est/disp/unres = 3/2/11 · confidence 0.32 · degraded False
  - authorities: 3 · counter-authorities: none · contrary terms: none
  - IRAC issue: What is the offence of rape under Ghanaian law?
  - IRAC rule: The Criminal Offences Act, 1960 (Act 29) provides general provisions for criminal offences, including attempts, abetment, conspiracy, and exemptions. Section 5 states: “This Part applies to other offences, except where a contrary intention appears.” Section 4 provides that “In the interpretation of…
  - firewall audit quick: {'checked': 3, 'unverified': 0, 'all_verified': True, 'unverified_displays': []}
  - firewall audit deep:  {'checked': 7, 'unverified': 0, 'all_verified': True, 'unverified_displays': []}

### human_rights — How are human rights enforced in Ghana?
- verdict: **GROUNDED** · retrieved sources: 3 · footer tags: ['PROPOSED', 'UNKNOWN']
- Quick (6.5s): In Ghana, human rights are enforced primarily through the judiciary, as provided for under Chapter V of the Constitution 1992 (as amended 1996). The Constitution guarantees fundamental human rights and freedoms, and individuals can seek redress in court if th…
  - quick contrary hints: ['amended']
- Deep (36.2s; retrieve None · advocate 9.529 · opponent 15.822 · judge 10.848): est/disp/unres = 8/4/4 · confidence 0.5 · degraded False
  - authorities: 6 · counter-authorities: ['Promotion of Proper Human Sexual Rights and Ghanaian Family Values Bill, 2021', 'Armed Forces Amendment Bill ,2022', 'Constitution Amendment Entrenched Provisions Bill, 2014'] · contrary terms: none
  - IRAC issue: How are human rights enforced in Ghana?
  - IRAC rule: The Constitution of the Republic of Ghana, 1992 (as amended to 1996), establishes fundamental human rights in Chapter V and provides for the enforcement of such rights through judicial review and administrative mechanisms. The Commission on Human Rights and Administrative Justice is established und…
  - firewall audit quick: {'checked': 0, 'unverified': 0, 'all_verified': True, 'unverified_displays': []}
  - firewall audit deep:  {'checked': 0, 'unverified': 0, 'all_verified': True, 'unverified_displays': []}

### civil_procedure — What are the civil procedure rules for summary judgment in Ghana?
- verdict: **GROUNDED** · retrieved sources: 3 · footer tags: ['CURRENT', 'PROPOSED']
- Quick (4.0s): The provided sources do not contain specific information on the civil procedure rules for summary judgment in Ghana. Source 1 (Courts Act 1993, Act 459) outlines the structure and jurisdiction of various courts in Ghana but does not detail procedural rules su…
- Deep (34.19s; retrieve None · advocate 7.234 · opponent 17.045 · judge 9.909): est/disp/unres = 4/6/5 · confidence 0.45 · degraded False
  - authorities: 6 · counter-authorities: ['Ghana Civil Aviation Bill, 2024', 'Courts Act, 1993 Act 459 (Revised)', 'High Court (Civil Procedure )(Amendment) rules , 2019 C.I.122'] · contrary terms: none
  - IRAC issue: What are the civil procedure rules for summary judgment in Ghana?
  - IRAC rule: The sources do not establish specific civil procedure rules for summary judgment in Ghana.
  - firewall audit quick: {'checked': 4, 'unverified': 1, 'all_verified': False, 'unverified_displays': ['Act 759']}
  - firewall audit deep:  {'checked': 12, 'unverified': 1, 'all_verified': False, 'unverified_displays': ['Act 759']}
    - quick residual verifier flags (corpus identity strings): ['Act 759']
    - deep residual verifier flags (corpus identity strings): ['Act 759']

### bill — What does the Promotion of Proper Human Sexual Rights and Ghanaian Family Values Bill 2021 provide?
- verdict: **GROUNDED** · retrieved sources: 3 · footer tags: ['CURRENT', 'PROPOSED']
- Quick (5.1s): The Promotion of Proper Human Sexual Rights and Ghanaian Family Values Bill, 2021 provides for: 1. **Prohibition of LGBTQ+ activities**: The Bill aims to proscribe LGBTQ+ and related activities (SOURCE 2). 2. **Prohibition of propaganda or advocacy for LGBTQ+…
- Deep (38.4s; retrieve None · advocate 9.837 · opponent 16.361 · judge 12.206): est/disp/unres = 3/5/7 · confidence 0.39 · degraded False
  - authorities: 4 · counter-authorities: ['Constitution Amendment Entrenched Provisions Bill, 2014'] · contrary terms: none
  - IRAC issue: What does the Promotion of Proper Human Sexual Rights and Ghanaian Family Values Bill 2021 provide?
  - IRAC rule: The Promotion of Proper Human Sexual Rights and Ghanaian Family Values Bill 2021 provides for proper human sexual rights and Ghanaian family values; proscribes LGBTQ+ and related activities; proscribes propaganda of, advocacy for or promotion of LGBTTQQIAAP+ and related activities; and provides for…
  - firewall audit quick: {'checked': 1, 'unverified': 0, 'all_verified': True, 'unverified_displays': []}
  - firewall audit deep:  {'checked': 3, 'unverified': 0, 'all_verified': True, 'unverified_displays': []}

### absurd_vacuous — ghana law
- verdict: **UNGROUNDED** · retrieved sources: 0 · footer tags: —
- Quick (0.0s): ⚖️ I couldn't find an authoritative Ghanaian source for that in my legal database, so I won't guess. Try rephrasing, or ask about a topic I cover (e.g. Criminal Offences Act, Contracts Act, Land Act, the 1992 Constitution).
- Deep: refused (no passes; 0.0s): ⚖️ I couldn't find an authoritative Ghanaian source for that in my legal database, so I won't guess. Try rephrasing, or ask about a topic I cover (e.g. Criminal Offences Act, Contracts Act, Land Act, the 1992 Constitution).

### absurd_nonsense — What is the Ghana tax treatment of quantum entanglement?
- verdict: **PARTIAL** · retrieved sources: 3 · footer tags: ['PROPOSED']
- Quick (5.84s): The Ghana tax treatment of quantum entanglement is **not addressed** in the provided sources. **Supporting Evidence:** - **Source 1 (Income Tax Amendment No.2 Bill, 2016)** discusses amendments to income tax laws regarding gifts tax and capital gains tax but …
  - quick contrary hints: ['amendment']
- Deep (35.06s; retrieve None · advocate 9.463 · opponent 14.172 · judge 11.428): est/disp/unres = 2/2/12 · confidence 0.3 · degraded False
  - authorities: 11 · counter-authorities: ['West African Gas Pipeline Act, 2004 (Act 681). Revised Edition', 'Value Added Tax (amendment) (NO.2)  Act, 2022 (ACT 1087)', 'REVENUE ADMINISTRATION BILL, 2016.pdf', 'Chartered Institute of Taxation Bill, 2014', 'National Health Insurance (Amendment) Act, 2018 (Act 971)', 'Electronic Transfer Levy Repeal Bill, 2025', 'Exemptions Bill ,2021', 'Valve Added Tax (Amendment) (No.2)Bill, 2022', 'Tax Amnesty Bill, 2017'] · contrary terms: none
  - IRAC issue: What is the Ghana tax treatment of quantum entanglement?
  - IRAC rule: The sources establish that the Ghana tax system, as defined in the Income Tax Act, 2015 ([unverified — not found in database]) and its amendments, does not explicitly address quantum entanglement. The Income Tax (Amendment) No. 2 Bill, 2016, clarifies that gifts are taxable only if received in resp…
  - firewall audit quick: {'checked': 0, 'unverified': 0, 'all_verified': True, 'unverified_displays': []}
  - firewall audit deep:  {'checked': 6, 'unverified': 2, 'all_verified': False, 'unverified_displays': ['Act 1087', 'Act 1087']}
    - deep residual verifier flags (corpus identity strings): ['Act 1087', 'Act 1087']

## Checks

- Bill question verdict **GROUNDED**, footer tags ['CURRENT', 'PROPOSED'] — PROPOSED bill labelled, not presented as enacted law.
- Absurd `absurd_vacuous` ("ghana law") verdict **UNGROUNDED** — refused, no passes/model call.
- Absurd `absurd_nonsense` ("What is the Ghana tax treatment of quantum entanglement?") verdict **PARTIAL** — ATTENTION: not refused (pre-existing retrieval fallback gap).
- Deep found counter-authorities for: ['human_rights', 'civil_procedure', 'bill', 'absurd_nonsense']