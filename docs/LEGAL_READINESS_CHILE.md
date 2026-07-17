# Chile legal-readiness review packet

Prepared: 17 July 2026

This is an engineering and product-gap review, not legal advice or a professional
legal opinion. A Chile-qualified consumer/privacy lawyer must approve the final
public wording before launch. The packet is intended to make that review fast and
specific.

## Product facts counsel should verify

- MachReach is a student SaaS with free, Plus, and Ultimate tiers.
- Lemon Squeezy is the payment processor/merchant flow; MachReach receives billing
  identifiers and subscription state, not card numbers.
- Users can cancel in product; paid access continues through the paid period.
- Account deletion first attempts to cancel active billing, then removes account
  data. It refuses deletion if provider cancellation fails.
- Users may provide identity, university, courses, grades, study activity, files,
  Canvas course data, social connections, and AI prompts/source material.
- Render hosts the service/database. OpenAI, Lemon Squeezy, optional PostHog,
  optional Sentry, SMTP, and Canvas/Instructure can process data.
- Terms currently set a minimum age of 16.

## Current-law and transition checklist

As of this packet date, Law 19,628 remains the operative general data-protection
law. Law 21,719 is scheduled to take effect on 1 December 2026 and substantially
changes the regime, including data-subject rights, controller duties,
international transfers, enforcement, and the new Data Protection Agency. Counsel
should review for both regimes now rather than create a second migration after
launch.

Official sources:

- [Current Law 19,628](https://www.bcn.cl/leychile/Navegar?idNorma=141599)
- [Law 21,719 and its deferred effective date](https://www.bcn.cl/leychile/navegar?idNorma=1209272)
- [Consumer Protection Law 19,496](https://www.bcn.cl/leychile/Navegar?idNorma=61438)
- [Electronic Commerce Regulation, Decree 6](https://www.bcn.cl/leychile/Navegar?idNorma=1165504)
- [Withdrawal-exclusion Regulation, Decree 52](https://www.bcn.cl/leychile/Navegar?idNorma=1206144)
- [SERNAC privacy/data-rights example](https://www.sernac.cl/portal/617/w3-article-53061.html)

## Blocking facts the owner and counsel must supply

The public documents cannot be finalized without:

1. Contracting provider's full legal name, RUT, legal form, and service address.
2. A Chile service/contact address and the official support/data-rights contact.
3. Confirmation of who is merchant of record and who issues legally required tax
   documents for each Chile sale.
4. Approved retention periods for accounts, uploaded materials, security logs,
   analytics, billing records, backups, and deleted-account tombstones.
5. Whether users under 18 are accepted, and the lawful age/consent design counsel
   approves for academic and potentially sensitive data.
6. The exact refund and withdrawal policy for digital services, including whether
   any lawful exclusion is used and how it is disclosed before payment.
7. Governing law, competent venue, and SERNAC dispute language approved by counsel.

## Privacy-policy changes for counsel to draft/approve

The current `/privacy` page is readable and lists data categories, purposes,
processors, security controls, export/deletion, and a contact address. It still
needs a formal addendum covering:

- controller identity and contact details;
- each purpose and lawful basis, including optional analytics consent;
- required versus optional data and the effect of refusing it;
- category-specific retention periods and backup deletion timing;
- recipients, processor roles, and international-transfer countries/safeguards;
- the complete procedure, identity verification, deadlines, and appeal/escalation
  route for access, rectification, deletion, objection, portability, and blocking;
- automated decisions/profiling, if leaderboard, fraud, or AI systems qualify;
- incident/breach handling and notification obligations;
- children's/teenagers' data and parental authorization where applicable;
- policy-change notice/versioning and the applicable effective date;
- a 1 December 2026 compliance section for Law 21,719.

Engineering evidence already present: JSON data export, permanent account
deletion, optional analytics consent, hashed passwords, encrypted credentials,
CSRF protection, rate limits, tenant tests, and provider-cancellation-before-delete.

## Terms, subscription, cancellation, and checkout changes

The Electronic Commerce Regulation requires clear pre-contract information and
defines total subscription cost per billing period. It also requires clear access
to contract terms and specific withdrawal information. Counsel should approve:

- provider identity, RUT, contact channel, and service address;
- plan price, currency, taxes, billing frequency, renewal behavior, and total cost
  per period before the checkout redirect;
- the precise start of paid access and the service's essential characteristics;
- a durable copy/confirmation of the accepted terms after contracting;
- a direct cancellation path, effective date, loss-of-access date, and confirmation;
- payment-failure, grace-period, recovery, upgrade, downgrade, and prorating rules;
- a legally accurate withdrawal/refund clause. Replace “refunds are handled case
  by case” with counsel-approved objective language;
- no waiver of mandatory consumer guarantees and a limitation-of-liability clause
  narrowed so it does not purport to exclude non-waivable Chilean rights;
- complaint handling, SERNAC rights, governing law, venue, and term-change notice;
- deletion consequences for paid plans, coins, study files, and records retained by
  the merchant/payment provider for legal reasons.

## Counsel acceptance record

Do not mark this review complete until a Chile-qualified lawyer records:

- reviewer name, firm, jurisdiction, and date;
- documents and product screens reviewed, including checkout/cancellation/deletion;
- approved redlines or final text;
- any residual risks and launch conditions;
- next review date, including a mandatory review before 1 December 2026.
