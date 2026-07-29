# Critical backend invariants

This document records the state transitions that must remain true when MachReach
is changed. Route payloads and visible behavior are compatibility boundaries;
the services named below own the side effects.

## AI usage

`student.ai_usage` is the only quota authority. A request key moves from absent
to `reserved`, then to exactly one of `settled` or `failed`. Reservation and
quota checks share one transaction and lock the student's quota scope.
Retries reuse the request key, settled result JSON is replayed, and artifacts
must use a stable feature/job key. A model call must not begin if reservation
cannot be verified. Stale reservations are recoverable and are surfaced by
`/health/operations`.

## Billing and real-money coin packs

Lemon Squeezy signatures are verified before the durable webhook inbox claim.
An event is processed once by provider event ID (or payload hash when the
provider omits it). Entitlements require the configured store, product, and
variant identifiers; invoice events without those fields may only update the
already-stored matching subscription ID. Provider timestamps prevent older
events from overwriting newer subscription truth.

A coin order credits once by provider order ID. The signed order's store,
product, and variant must match the requested pack. Refund reconciliation and
wallet/debt changes are transactional and monotonic.

## Leaderboard payouts and referrals

A closed leaderboard period is claimed before calculation. The payout run,
unique prize row, and wallet credit commit together. Catch-up advances only
after the period completes. Email delivery uses a durable idempotent outbox.

Referral redemption locks the relevant identities and commits the referral,
entitlement extension, reward audit, and pending-marker removal together.
Rolling caps can withhold rewards without losing the audit trail; revocation is
an explicit administrative transition.

## Academic identity and benchmarks

Profile writes validate country-university-major relationships in one
transaction. University changes that affect competitive placement are audited
and subject to the configured cooldown. Historical course outcomes retain the
university and canonical course identity captured when they were recorded.

Benchmarks are partitioned by `university_id`, `canonical_course_id`, and
cohort/version. Ambiguous outcomes are suppressed. Only distinct students count
toward the anonymity threshold.

## Canvas and account privacy

Canvas connect tokens identify one MachReach client, expire after fifteen
minutes, and include a revocable token version. Repeated imports reconcile by
stable Canvas identity and cannot mutate another client's courses. Disconnect
revokes outstanding tokens and archives imported courses.

Account export must include every student-owned normalized ledger and audit
table. Account deletion is provider-first for active billing and relies on
foreign-key cascades for student-owned rows.

## Time

Persisted instants are UTC-aware. Calendar days, weeks, and months are derived
only from the saved IANA timezone through `student.periods`. Tests inject a
clock with `use_clock`; reward, quota, or entitlement decisions must never
trust a browser date.

## Coverage scope

Python coverage excludes only functions whose role is page presentation
composition (`*_page`, `_render`, and `_s_render`). Those functions contain the
locked inline UI and read-only view-model assembly and are exercised through
the full Playwright desktop/mobile matrix. JSON APIs, state-changing routes,
authentication, billing, AI, rewards, workers, persistence, and service modules
remain in the meaningful backend coverage denominator.
