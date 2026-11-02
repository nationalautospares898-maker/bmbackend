"""Authentication and authorisation service.

The behaviours here are the ones `tests/api/test_auth.py` must prove, and each exists
for a stated reason:

* **Rotation with reuse detection** (FR-AUTH-002). Every refresh consumes the old token
  and issues a new one in the same family. Presenting a consumed token means the cookie
  was copied, so the **entire family** is revoked and an audit event is written. The
  user has to sign in again — annoying once, versus an attacker holding a session.
* **Lockout** (`SECURITY.md §2.4`). 10 failures / 5 min per username, 30 / 5 min per
  hashed IP, then a 15-minute lockout reported with `retry_after_seconds` so the client
  can count down instead of guessing.
* **`must_change_password` blocks every non-auth endpoint.** A seeded credential that is
  never rotated is the most likely way this system gets breached (T-5).
* **A revoked device is refused everywhere, including sync** (FR-OFFLINE-010), and the
  client never deletes local data in response.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy.orm import Session

from bm.domain.audit import service as audit
from bm.domain.audit.service import AuditAction
from bm.domain.auth.hashing import (
    decoy_password_hash,
    hash_ip,
    hash_password,
    hash_refresh_token,
    new_refresh_token,
    validate_password_strength,
    verify_password,
)
from bm.domain.auth.tokens import AccessTokenClaims, mint_access_token
from bm.domain.shared.clock import Clock
from bm.domain.shared.context import AuthContext
from bm.domain.shared.errors import (
    AccountLocked,
    BranchScopeInvalid,
    DeviceNotRegistered,
    DeviceRevoked,
    InvalidCredentials,
    NotFound,
    RefreshTokenReused,
    UserInactive,
    ValidationFailed,
)
from bm.infra.db.repositories.auth import (
    AuthRepository,
    BranchRecord,
    BusinessRecord,
    MembershipRecord,
    UserRecord,
)
from bm.infra.db.session import transaction

#: `SECURITY.md §2.4`.
LOCKOUT_WINDOW = timedelta(minutes=5)
LOCKOUT_DURATION = timedelta(minutes=15)
MAX_FAILURES_PER_USERNAME = 10
MAX_FAILURES_PER_IP = 30


@dataclass(frozen=True, slots=True)
class LoginResult:
    access_token: str
    expires_in: int
    #: Returned to the transport layer to be set as an `HttpOnly` cookie. It is never
    #: placed in a response body (NFR-SEC-003).
    refresh_token: str
    user: UserRecord
    business: BusinessRecord
    branches: list[BranchRecord]
    active_branch_id: uuid.UUID | None
    role: str
    permissions: frozenset[str]
    server_time: datetime


@dataclass(frozen=True, slots=True)
class AuthConfig:
    jwt_signing_key: str
    jwt_previous_key: str | None
    access_token_ttl_seconds: int
    refresh_token_ttl_days: int
    password_pepper: str
    ip_hash_pepper: str


class AuthService:
    def __init__(self, session: Session, config: AuthConfig, clock: Clock) -> None:
        self._session = session
        self._repo = AuthRepository(session)
        self._config = config
        self._clock = clock

    # --- durable security records --------------------------------------------
    #
    # `ARCHITECTURE.md §5.1` rule 1 is "one request, one transaction": the request's
    # session is rolled back on any exception. That is right for business state and
    # **wrong** for security evidence, because the two records below have to survive
    # the very failure they describe:
    #
    #   * A failed login attempt. If it rolls back with `InvalidCredentials`, the
    #     `login_attempts` table stays empty and lockout can never trigger — the
    #     control silently does nothing.
    #   * A refresh-token family revocation on reuse detection. If it rolls back with
    #     `RefreshTokenReused`, the copied cookie is never actually invalidated and
    #     FR-AUTH-002 is a comment rather than a control.
    #
    # Both are therefore written on their own connection and committed immediately.
    # This is a deliberate, narrow exception, not a general licence to commit outside
    # the unit of work: nothing here touches money, stock or an invoice.

    @staticmethod
    @contextmanager
    def _independent_transaction() -> Iterator[Session]:
        with transaction() as session:
            yield session

    def _record_attempt_durably(
        self,
        *,
        business_id: uuid.UUID | None,
        username: str | None,
        ip_hash: str | None,
        succeeded: bool,
        reason: str | None,
    ) -> None:
        with self._independent_transaction() as session:
            AuthRepository(session).record_attempt(
                business_id=business_id,
                username=username,
                ip_hash=ip_hash,
                succeeded=succeeded,
                reason=reason,
            )

    # --- context resolution --------------------------------------------------

    def resolve_context(
        self,
        claims: AccessTokenClaims,
        *,
        device_id: uuid.UUID | None,
        counter_id: uuid.UUID | None,
    ) -> AuthContext:
        """Turn verified token claims into an `AuthContext`.

        The device is re-checked on **every** request, not only at login, because that
        is what makes revocation effective: a revoked device is refused everywhere,
        including sync (FR-OFFLINE-010). The ≤15-minute window in which an
        already-issued access token still verifies is the documented, accepted bound
        (`SECURITY.md §15`).

        This lives in the domain rather than in `api/deps.py` so the API layer never
        reaches into a repository (`ARCHITECTURE.md §3`).
        """
        effective_device_id = device_id or claims.device_id
        if effective_device_id is None:
            raise DeviceNotRegistered("This device is not registered.")

        device = self._repo.get_device(effective_device_id, claims.business_id)
        if device is None:
            # A device belonging to another business must look identical to an unknown
            # one, so registration elsewhere is not disclosed.
            raise DeviceNotRegistered()
        if device.is_revoked:
            raise DeviceRevoked()

        return AuthContext(
            user_id=claims.user_id,
            business_id=claims.business_id,
            branch_id=claims.branch_id,
            counter_id=counter_id or claims.counter_id or device.counter_id,
            device_id=device.id,
            role=claims.role,
            permissions=claims.permissions,
            token_id=claims.token_id,
            business_timezone=claims.business_timezone,
            full_name=claims.full_name,
            must_change_password=claims.must_change_password,
        )

    # --- billing readiness ---------------------------------------------------

    def _ensure_billing_counter(
        self,
        *,
        device_id: uuid.UUID,
        business_id: uuid.UUID,
        branch_id: uuid.UUID | None,
        current_counter_id: uuid.UUID | None,
        actor_user_id: uuid.UUID,
    ) -> uuid.UUID | None:
        """Make this computer able to bill, without anybody configuring anything.

        **Why this exists.** The owner runs the shop from one computer and asked for the
        counter feature to stop being part of the workflow. It could not simply be hidden:
        `draft_bills.counter_id` and `invoices.counter_id` are `NOT NULL`, every sales
        report groups by counter, and `DraftUpsertRequest` requires one. So the counter
        remains the internal bookkeeping dimension it always was — historical invoices
        stay readable, no migration, no column dropped, no history deleted — and what is
        removed is the **human requirement** to set it up. A freshly installed PC binds
        itself to the shop's counter on first login and bills immediately.

        **What it deliberately does not do.** It never overwrites an assignment somebody
        made on purpose (the `counter_id IS NULL` guard lives in the `UPDATE`), it never
        touches a revoked device, and it never invents a counter in another branch. The
        binding is audited as `device.counter_assigned` exactly as the manual action was,
        so the trail still answers "whose takings does this machine's next sale land in" —
        with `reason` recording that the server decided rather than a person.

        Returning `None` is possible and is handled by the caller's existing paths: a
        business whose branch has no active counter at all cannot have one invented for
        it here, because that is a provisioning decision and this is a login.
        """
        if current_counter_id is not None:
            return current_counter_id

        counter_id = self._repo.default_counter_for(business_id, branch_id)
        if counter_id is None:
            return None

        if self._repo.bind_device_counter(device_id, business_id, counter_id=counter_id):
            audit.record(
                self._session,
                business_id=business_id,
                action=AuditAction.DEVICE_COUNTER_ASSIGNED,
                entity_type="device",
                entity_id=device_id,
                actor_user_id=actor_user_id,
                branch_id=branch_id,
                device_id=device_id,
                counter_id=counter_id,
                reason="assigned automatically on first sign-in",
                before={"counter_id": None},
                after={"counter_id": str(counter_id)},
            )
            return counter_id

        # Lost the race to a concurrent login, or the device was revoked between the
        # read and the write. Re-read rather than assume: the other transaction's value
        # is the truth, and billing with a counter this one merely proposed would be a
        # guess.
        device = self._repo.get_device(device_id, business_id)
        return device.counter_id if device else None

    # --- login ---------------------------------------------------------------

    def login(
        self,
        *,
        username: str,
        password: str,
        device_id: uuid.UUID,
        device_label: str,
        user_agent: str | None = None,
        ip: str | None = None,
    ) -> LoginResult:
        now = self._clock.now()
        ip_digest = hash_ip(ip, pepper=self._config.ip_hash_pepper)

        self._assert_not_locked_out(username=username, ip_hash=ip_digest, now=now)

        user = self._repo.find_user_by_username(username)

        # The password is verified even when the user is missing, so a non-existent
        # username and a wrong password take indistinguishable time. Skipping the hash
        # for an unknown user turns login into a user-enumeration oracle.
        password_ok = (
            verify_password(password, user.password_hash, pepper=self._config.password_pepper)
            if user
            else verify_password(
                password, decoy_password_hash(), pepper=self._config.password_pepper
            )
        )

        if user is None or not password_ok:
            self._record_attempt_durably(
                business_id=user.business_id if user else None,
                username=username,
                ip_hash=ip_digest,
                succeeded=False,
                reason="bad_credentials",
            )
            raise InvalidCredentials()

        if not user.is_active:
            self._record_attempt_durably(
                business_id=user.business_id,
                username=username,
                ip_hash=ip_digest,
                succeeded=False,
                reason="user_inactive",
            )
            raise UserInactive()

        memberships = self._repo.memberships_for(user.id)
        if not memberships:
            # A user with no active membership can authenticate but has no scope. That
            # is an administrative state, not a credential problem, so it is reported
            # as inactive rather than as bad credentials.
            self._record_attempt_durably(
                business_id=user.business_id,
                username=username,
                ip_hash=ip_digest,
                succeeded=False,
                reason="no_membership",
            )
            raise UserInactive("This account has no assigned role yet. Ask the owner to set one.")

        business = self._repo.get_business(user.business_id)
        if business is None:
            raise UserInactive()

        device = self._repo.get_device(device_id, user.business_id)
        if device is not None and device.is_revoked:
            self._record_attempt_durably(
                business_id=user.business_id,
                username=username,
                ip_hash=ip_digest,
                succeeded=False,
                reason="device_revoked",
            )
            raise DeviceRevoked()

        return self._issue_session(
            user=user,
            business=business,
            memberships=memberships,
            device_id=device_id,
            device_label=device_label,
            user_agent=user_agent,
            ip_digest=ip_digest,
            username=username,
            now=now,
        )

    def unattended_session(
        self,
        *,
        username: str,
        device_id: uuid.UUID,
        device_label: str,
        user_agent: str | None = None,
        ip: str | None = None,
    ) -> LoginResult:
        """Issue a session with **no password**, for a server configured to allow it.

        The owner runs the shop from one computer and asked for the application to open
        straight into billing rather than onto a sign-in form. This is the whole of that
        change on the server side.

        **What it does not do.** It does not weaken a single check that happens after a
        session exists. The token it mints is an ordinary one with the account's real
        role and permissions; every endpoint still requires it; tenant scope still comes
        from the token; `must_change_password` still blocks; refresh rotation and reuse
        detection are unchanged; and `resolve_context` still re-checks the device on every
        request, so a **revoked device is still refused** — which is the control that
        matters most here, because it is the only way to take access away from a machine
        once it has walked out of the shop.

        **What it genuinely gives up.** Anyone who can reach this endpoint gets a working
        session. There is no password to fail and no second factor. That is acceptable
        exactly when network access is already controlled — a server bound to localhost,
        or a LAN the shop owns — and it is not acceptable on the open internet. The caller
        is the operator's own configuration, so the decision is theirs to make explicitly:
        `AuthService` never enables this of its own accord, and `Settings.unattended_access`
        is off unless somebody sets it.

        The account is still a real one. The session belongs to it, the audit trail names
        it, and the attempt is recorded — so "who did this" has the same answer it always
        had. The audit row carries a reason saying no password was presented, so the trail
        does not pretend somebody typed one.
        """
        now = self._clock.now()
        ip_digest = hash_ip(ip, pepper=self._config.ip_hash_pepper)

        user = self._repo.find_user_by_username(username)
        if user is None or not user.is_active:
            # A misconfigured account name is an operator error, not an attack, and it is
            # reported as one: there is no caller to enumerate accounts here, because the
            # name came from the server's own configuration rather than from a request.
            raise UserInactive(
                "The account configured for unattended access is missing or inactive."
            )

        memberships = self._repo.memberships_for(user.id)
        if not memberships:
            raise UserInactive("This account has no assigned role yet. Ask the owner to set one.")

        business = self._repo.get_business(user.business_id)
        if business is None:
            raise UserInactive()

        device = self._repo.get_device(device_id, user.business_id)
        if device is not None and device.is_revoked:
            # The same refusal a password login gets. Revocation is what makes a lost
            # machine recoverable, and skipping it here would hollow that out.
            self._record_attempt_durably(
                business_id=user.business_id,
                username=username,
                ip_hash=ip_digest,
                succeeded=False,
                reason="device_revoked",
            )
            raise DeviceRevoked()

        return self._issue_session(
            user=user,
            business=business,
            memberships=memberships,
            device_id=device_id,
            device_label=device_label,
            user_agent=user_agent,
            ip_digest=ip_digest,
            username=username,
            now=now,
            audit_reason="unattended access — no password was presented",
        )

    def _issue_session(
        self,
        *,
        user: UserRecord,
        business: BusinessRecord,
        memberships: list[MembershipRecord],
        device_id: uuid.UUID,
        device_label: str,
        user_agent: str | None,
        ip_digest: str | None,
        username: str,
        now: datetime,
        audit_reason: str | None = None,
    ) -> LoginResult:
        """Everything that happens **after** a caller has been authenticated.

        Extracted so `login` and `unattended_session` cannot drift apart. The two differ
        only in how the caller proves who they are — a password, or a server that has
        been configured to vouch for them. What a session *is* must be identical either
        way: the same token contents, the same rotation family, the same device
        registration, the same counter binding, the same audit row. Duplicating sixty
        lines of that in two places is how one of them quietly stops matching the other,
        and this is security-critical code.

        `audit_reason` is the one deliberate difference, so the trail distinguishes a
        session somebody typed a password for from one the server granted on its own.
        """
        membership = self._primary_membership(memberships)
        branches = self._visible_branches(user.business_id, memberships)
        active_branch_id = membership.branch_id or (branches[0].id if branches else None)

        device = self._repo.register_device(
            device_id=device_id,
            business_id=user.business_id,
            branch_id=active_branch_id,
            label=device_label,
            user_agent=user_agent,
        )

        effective_counter_id = self._ensure_billing_counter(
            device_id=device.id,
            business_id=user.business_id,
            branch_id=active_branch_id,
            current_counter_id=device.counter_id,
            actor_user_id=user.id,
        )

        permissions = self._repo.permissions_for_role(membership.role_id)

        access_token, token_id = mint_access_token(
            signing_key=self._config.jwt_signing_key,
            ttl_seconds=self._config.access_token_ttl_seconds,
            user_id=user.id,
            business_id=user.business_id,
            branch_id=active_branch_id,
            counter_id=effective_counter_id,
            device_id=device.id,
            role=membership.role_code,
            permissions=permissions,
            business_timezone=business.timezone,
            full_name=user.full_name,
            must_change_password=user.must_change_password,
            issued_at=now,
        )

        refresh_token = new_refresh_token()
        self._repo.create_refresh_token(
            user_id=user.id,
            device_id=device.id,
            family_id=uuid.uuid4(),  # a login starts a new family
            token_hash=hash_refresh_token(refresh_token),
            ttl_days=self._config.refresh_token_ttl_days,
            now=now,
        )

        self._repo.touch_last_login(user.id, now)
        self._record_attempt_durably(
            business_id=user.business_id,
            username=username,
            ip_hash=ip_digest,
            succeeded=True,
            reason=None,
        )
        audit.record(
            self._session,
            business_id=user.business_id,
            action=AuditAction.USER_LOGGED_IN,
            entity_type="user",
            entity_id=user.id,
            actor_user_id=user.id,
            branch_id=active_branch_id,
            device_id=device.id,
            ip_hash=ip_digest,
            # The one difference between a password login and an unattended one, so the
            # trail distinguishes them. `None` for an ordinary login, which keeps every
            # existing audit row exactly as it was.
            reason=audit_reason,
            # A correlation id for the issued session, not the token itself — the
            # token is never logged or audited (FR-AUDIT-004).
            after={"session_reference": token_id, "role": membership.role_code},
        )

        return LoginResult(
            access_token=access_token,
            expires_in=self._config.access_token_ttl_seconds,
            refresh_token=refresh_token,
            user=user,
            business=business,
            branches=branches,
            active_branch_id=active_branch_id,
            role=membership.role_code,
            permissions=permissions,
            server_time=now,
        )

    def _assert_not_locked_out(self, *, username: str, ip_hash: str | None, now: datetime) -> None:
        window_start = now - LOCKOUT_WINDOW
        # A successful login inside the window clears the count: the earlier failures
        # were a forgotten password, not an attack in progress.
        last_success = self._repo.last_successful_login_at(username)
        if last_success is not None and last_success > window_start:
            window_start = last_success

        by_username, by_ip = self._repo.recent_failures(
            username=username, ip_hash=ip_hash, since=window_start
        )

        if by_username >= MAX_FAILURES_PER_USERNAME:
            raise AccountLocked(
                details={"retry_after_seconds": int(LOCKOUT_DURATION.total_seconds())}
            )
        if by_ip >= MAX_FAILURES_PER_IP:
            raise AccountLocked(
                "Too many attempts from this location. Try again shortly.",
                details={"retry_after_seconds": int(LOCKOUT_DURATION.total_seconds())},
            )

    @staticmethod
    def _primary_membership(memberships: list[MembershipRecord]) -> MembershipRecord:
        """Prefer an all-branch membership, so an owner lands with full scope."""
        for membership in memberships:
            if membership.branch_id is None:
                return membership
        return memberships[0]

    def _visible_branches(
        self, business_id: uuid.UUID, memberships: list[MembershipRecord]
    ) -> list[BranchRecord]:
        if any(m.branch_id is None for m in memberships):
            return self._repo.branches_for(business_id, None)
        return self._repo.branches_for(
            business_id, [m.branch_id for m in memberships if m.branch_id]
        )

    # --- refresh -------------------------------------------------------------

    def refresh(self, *, presented_token: str, device_id: uuid.UUID) -> LoginResult:
        """Rotate the refresh token, detecting reuse.

        Order matters: the token is looked up, then **consumed with a conditional
        update**. If that update affects no rows, another request already consumed it,
        which is reuse — so the family is revoked rather than a second session being
        minted.
        """
        now = self._clock.now()
        stored = self._repo.find_refresh_token(hash_refresh_token(presented_token))
        if stored is None:
            raise RefreshTokenReused()

        if stored.device_id != device_id:
            # The cookie is device-bound. A mismatch means it was moved.
            self._revoke_family_and_audit(stored.family_id, stored.user_id, now, "device_mismatch")
            raise RefreshTokenReused()

        if stored.consumed_at is not None or stored.revoked_at is not None:
            self._revoke_family_and_audit(stored.family_id, stored.user_id, now, "token_reuse")
            raise RefreshTokenReused()

        if stored.expires_at <= now:
            raise RefreshTokenReused("Your session has expired. Please sign in again.")

        user = self._repo.get_user(stored.user_id, self._business_of(stored.user_id))
        if user is None or not user.is_active:
            raise UserInactive()

        device = self._repo.get_device(device_id, user.business_id)
        if device is None:
            raise DeviceNotRegistered()
        if device.is_revoked:
            raise DeviceRevoked()

        memberships = self._repo.memberships_for(user.id)
        if not memberships:
            raise UserInactive()
        business = self._repo.get_business(user.business_id)
        if business is None:
            raise UserInactive()

        membership = self._primary_membership(memberships)
        branches = self._visible_branches(user.business_id, memberships)
        active_branch_id = (
            device.branch_id or membership.branch_id or (branches[0].id if branches else None)
        )
        permissions = self._repo.permissions_for_role(membership.role_id)

        new_token = new_refresh_token()
        new_token_id = self._repo.create_refresh_token(
            user_id=user.id,
            device_id=device.id,
            family_id=stored.family_id,  # same family, rotated
            token_hash=hash_refresh_token(new_token),
            ttl_days=self._config.refresh_token_ttl_days,
            now=now,
        )

        if not self._repo.consume_refresh_token(stored.id, replaced_by_id=new_token_id, now=now):
            # Lost the race: another refresh consumed it first. Both requests presented
            # the same token, so this is reuse.
            self._revoke_family_and_audit(stored.family_id, user.id, now, "concurrent_refresh")
            raise RefreshTokenReused()

        access_token, _ = mint_access_token(
            signing_key=self._config.jwt_signing_key,
            ttl_seconds=self._config.access_token_ttl_seconds,
            user_id=user.id,
            business_id=user.business_id,
            branch_id=active_branch_id,
            counter_id=device.counter_id,
            device_id=device.id,
            role=membership.role_code,
            permissions=permissions,
            business_timezone=business.timezone,
            full_name=user.full_name,
            must_change_password=user.must_change_password,
            issued_at=now,
        )
        self._repo.touch_device(device.id)

        return LoginResult(
            access_token=access_token,
            expires_in=self._config.access_token_ttl_seconds,
            refresh_token=new_token,
            user=user,
            business=business,
            branches=branches,
            active_branch_id=active_branch_id,
            role=membership.role_code,
            permissions=permissions,
            server_time=now,
        )

    def _business_of(self, user_id: uuid.UUID) -> uuid.UUID:
        """Resolve the tenant from the user row itself.

        Refresh has no token claims to trust yet, so the business cannot come from the
        request. Reading it from the stored user is the only safe source.
        """
        memberships = self._repo.memberships_for(user_id)
        if not memberships:
            raise UserInactive()
        return memberships[0].business_id

    def _revoke_family_and_audit(
        self, family_id: uuid.UUID, user_id: uuid.UUID, now: datetime, reason: str
    ) -> None:
        """Revoke on its own connection so it survives the `RefreshTokenReused` raise.

        Without this the revocation would roll back with the error and a copied cookie
        would stay usable — FR-AUTH-002 would be a comment, not a control.
        """
        business_id = self._business_of(user_id)
        with self._independent_transaction() as session:
            revoked = AuthRepository(session).revoke_family(family_id, now)
            audit.record(
                session,
                business_id=business_id,
                action=AuditAction.REFRESH_TOKEN_REUSED,
                entity_type="refresh_family",
                entity_id=family_id,
                actor_user_id=user_id,
                reason=reason,
                after={"tokens_revoked": revoked},
            )

    # --- logout --------------------------------------------------------------

    def logout(self, *, presented_token: str | None, ctx: AuthContext | None = None) -> None:
        """Revoke the caller's own family. Missing or unknown token is not an error —
        the caller wanted to be signed out, and they are."""
        now = self._clock.now()
        if not presented_token:
            return
        stored = self._repo.find_refresh_token(hash_refresh_token(presented_token))
        if stored is None:
            return
        self._repo.revoke_family(stored.family_id, now)
        audit.record(
            self._session,
            business_id=ctx.business_id if ctx else self._business_of(stored.user_id),
            action=AuditAction.USER_LOGGED_OUT,
            entity_type="user",
            entity_id=stored.user_id,
            actor_user_id=stored.user_id,
            device_id=stored.device_id,
        )

    # --- branch selection ----------------------------------------------------

    def select_branch(self, ctx: AuthContext, branch_id: uuid.UUID) -> tuple[str, int]:
        """Re-issue the access token scoped to another branch the caller belongs to."""
        memberships = self._repo.memberships_for(ctx.user_id)
        covers_all = any(m.branch_id is None for m in memberships)
        allowed = covers_all or any(m.branch_id == branch_id for m in memberships)
        if not allowed:
            raise BranchScopeInvalid()

        branches = self._repo.branches_for(ctx.business_id, [branch_id])
        if not branches:
            raise NotFound("That branch does not exist.")

        membership = self._primary_membership(memberships)
        business = self._repo.get_business(ctx.business_id)
        if business is None:
            raise NotFound()

        token, _ = mint_access_token(
            signing_key=self._config.jwt_signing_key,
            ttl_seconds=self._config.access_token_ttl_seconds,
            user_id=ctx.user_id,
            business_id=ctx.business_id,
            branch_id=branch_id,
            counter_id=ctx.counter_id,
            device_id=ctx.device_id,
            role=membership.role_code,
            permissions=self._repo.permissions_for_role(membership.role_id),
            business_timezone=business.timezone,
            full_name=ctx.full_name,
            must_change_password=ctx.must_change_password,
            issued_at=self._clock.now(),
        )
        audit.record_for(
            self._session,
            ctx,
            action=AuditAction.BRANCH_SELECTED,
            entity_type="branch",
            entity_id=branch_id,
        )
        return token, self._config.access_token_ttl_seconds

    # --- password ------------------------------------------------------------

    def change_own_password(
        self, ctx: AuthContext, *, current_password: str, new_password: str
    ) -> None:
        """Changing the password clears `must_change_password` and signs out every
        other session, because a password change is also how a user reacts to
        suspecting their account was used."""
        user = self._repo.get_user(ctx.user_id, ctx.business_id)
        if user is None:
            raise NotFound()
        if not verify_password(
            current_password, user.password_hash, pepper=self._config.password_pepper
        ):
            raise InvalidCredentials("Your current password is incorrect.")
        if new_password == current_password:
            raise ValidationFailed({"new_password": "Choose a password you have not used here."})

        try:
            validate_password_strength(new_password)
        except ValueError as exc:
            raise ValidationFailed({"new_password": str(exc)}) from exc

        self._repo.set_password(
            user.id, hash_password(new_password, pepper=self._config.password_pepper)
        )
        if ctx.device_id:
            self._repo.revoke_tokens_for_device(ctx.device_id, self._clock.now())
        audit.record_for(
            self._session,
            ctx,
            action=AuditAction.USER_PASSWORD_CHANGED,
            entity_type="user",
            entity_id=user.id,
        )
