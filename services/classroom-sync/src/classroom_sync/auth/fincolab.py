"""Server-side FinColab identity, ownership, and roster verification."""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass, field
from hashlib import sha256
from time import monotonic

import httpx

from classroom_sync.auth.student_binding import (
    parse_legacy_child_name,
    parse_student_binding_description,
    safe_legacy_key,
)
from classroom_sync.errors import (
    AuthenticationError,
    AuthorizationError,
    RosterConflictError,
    UpstreamContractError,
    UpstreamUnavailableError,
)

TEACHER_ROLE_NAMES = frozenset({"teacher", "admin", "administrator", "owner", "manager"})
STUDENT_ROLE_NAMES = frozenset({"student"})
PARENT_PROJECT_PATTERN = re.compile(r"^\[FINCOLAB_PARENT_PROJECT_ID:([^\]\r\n]+)\]")


@dataclass(frozen=True)
class Principal:
    """A user identity resolved from FinColab, including a non-loggable bearer token."""

    user_id: str
    username: str
    bearer_token: str = field(repr=False, compare=False)


@dataclass(frozen=True)
class SpaceMember:
    user_id: str
    username: str
    role_name: str


@dataclass(frozen=True)
class StudentChildExperiment:
    student_id: str
    student_username: str
    child_algorithm_id: str
    workbench_id: str


class FincolabIdentityGateway:
    """Verify every classroom actor against read-only FinColab APIs."""

    def __init__(
        self,
        *,
        base_url: str,
        organization_id: str,
        client: httpx.Client,
        student_project_name_prefix: str = "exp",
        cache_ttl_seconds: float = 30.0,
        clock: Callable[[], float] = monotonic,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._organization_id = organization_id
        self._client = client
        self._student_project_name_prefix = student_project_name_prefix
        self._cache_ttl_seconds = cache_ttl_seconds
        self._clock = clock
        self._principals_by_token_hash: dict[str, tuple[float, Principal]] = {}

    def resolve_principal(self, bearer_token: str) -> Principal:
        """Resolve the current bearer through FinColab and cache only its hash briefly."""

        token = bearer_token.strip()
        if not token:
            raise AuthenticationError("missing_bearer_token")

        token_hash = sha256(token.encode("utf-8")).hexdigest()
        cached = self._principals_by_token_hash.get(token_hash)
        now = self._clock()
        if cached is not None and cached[0] > now:
            return cached[1]

        user = self._request_object("/v1/user/info", token)
        principal = Principal(
            user_id=self._required_string(user, "id", "upstream_user_missing_id"),
            username=self._required_string(user, "username", "upstream_user_missing_username"),
            bearer_token=token,
        )
        self._principals_by_token_hash[token_hash] = (now + self._cache_ttl_seconds, principal)
        return principal

    def require_teacher_owner(
        self,
        principal: Principal,
        space_id: str,
        experiment_id: str,
    ) -> None:
        """Require a teacher roster role and exact ownership of the parent experiment."""

        member = self._require_space_member(principal, space_id)
        if member.role_name.casefold() not in TEACHER_ROLE_NAMES:
            raise AuthorizationError("teacher_role_required")

        experiment = self._request_object(
            f"/v1/spaces/{space_id}/algorithm_development/{experiment_id}",
            principal.bearer_token,
        )
        owner_username = experiment.get("username")
        if not isinstance(owner_username, str) or not owner_username.strip():
            raise AuthorizationError("experiment_owner_unverified")
        if owner_username != principal.username:
            raise AuthorizationError("experiment_owner_mismatch")

    def require_student_member(self, principal: Principal, space_id: str) -> None:
        """Require a trusted student membership before exposing a student assignment."""

        member = self._require_space_member(principal, space_id)
        if member.role_name.casefold() not in STUDENT_ROLE_NAMES:
            raise AuthorizationError("student_role_required")

    def list_student_children(
        self,
        principal: Principal,
        space_id: str,
        parent_algorithm_id: str,
    ) -> tuple[StudentChildExperiment, ...]:
        """Return one verified child experiment per student, or fail closed on conflicts."""

        students_by_id = {
            member.user_id: member
            for member in self._list_space_members(principal.bearer_token, space_id)
            if member.role_name.casefold() in STUDENT_ROLE_NAMES
        }
        legacy_students_by_key: dict[str, SpaceMember] = {}
        for member in students_by_id.values():
            key = safe_legacy_key(member.username)
            if key in legacy_students_by_key:
                raise RosterConflictError("legacy_safe_key_collision")
            legacy_students_by_key[key] = member
        projects = self._list_paginated(
            f"/v1/spaces/{space_id}/algorithm_development", principal.bearer_token
        )

        children: list[StudentChildExperiment] = []
        seen_student_ids: set[str] = set()
        seen_child_ids: set[str] = set()
        seen_workbench_ids: set[str] = set()
        for project in projects:
            description = project.get("description")
            if not isinstance(description, str):
                continue
            parent_match = PARENT_PROJECT_PATTERN.match(description)
            if parent_match is None or parent_match.group(1) != parent_algorithm_id:
                continue

            binding = parse_student_binding_description(description)
            if binding is None:
                name = project.get("name")
                if not isinstance(name, str):
                    continue
                legacy_key = parse_legacy_child_name(name, self._student_project_name_prefix)
                if legacy_key is None:
                    continue
                legacy_member = legacy_students_by_key.get(legacy_key)
                if legacy_member is None:
                    continue
                member = legacy_member
            else:
                if binding.space_id != space_id:
                    raise RosterConflictError("student_binding_space_mismatch")
                if binding.parent_algorithm_id != parent_algorithm_id:
                    raise RosterConflictError("student_binding_parent_mismatch")
                bound_member = students_by_id.get(binding.student_id)
                if bound_member is None:
                    raise RosterConflictError("student_binding_student_not_in_roster")
                member = bound_member
                if member.username != binding.student_username:
                    raise RosterConflictError("student_binding_username_mismatch")

            child_id = self._required_string(project, "id", "child_algorithm_missing_id")
            workbench_id_value = project.get("workbench_id")
            owner_username = project.get("username")
            detail: dict[str, object] | None = None
            if (
                not isinstance(workbench_id_value, str)
                or not workbench_id_value.strip()
                or not isinstance(owner_username, str)
                or not owner_username.strip()
            ):
                detail = self._request_object(
                    f"/v1/spaces/{space_id}/algorithm_development/{child_id}", principal.bearer_token
                )
            if not isinstance(workbench_id_value, str) or not workbench_id_value.strip():
                workbench_id = self._required_string(
                    detail or {}, "workbench_id", "child_workbench_unverified"
                )
            else:
                workbench_id = workbench_id_value
            if not isinstance(owner_username, str) or not owner_username.strip():
                owner_username = self._required_string(detail, "username", "child_owner_unverified")
            if owner_username not in {principal.username, member.username}:
                raise RosterConflictError("child_owner_contract_conflict")
            child = StudentChildExperiment(member.user_id, member.username, child_id, workbench_id)
            if child.student_id in seen_student_ids:
                raise RosterConflictError("duplicate_student_child")
            if child.child_algorithm_id in seen_child_ids:
                raise RosterConflictError("duplicate_child_algorithm")
            if child.workbench_id in seen_workbench_ids:
                raise RosterConflictError("duplicate_workbench")
            seen_student_ids.add(child.student_id)
            seen_child_ids.add(child.child_algorithm_id)
            seen_workbench_ids.add(child.workbench_id)
            children.append(child)

        return tuple(children)

    def _require_space_member(self, principal: Principal, space_id: str) -> SpaceMember:
        members = self._list_space_members(principal.bearer_token, space_id)
        for member in members:
            if member.user_id == principal.user_id and member.username == principal.username:
                return member
        raise AuthorizationError("space_membership_required")

    def _list_space_members(self, bearer_token: str, space_id: str) -> tuple[SpaceMember, ...]:
        rows = self._list_paginated(
            f"/v1/organizations/{self._organization_id}/spaces/{space_id}/users", bearer_token
        )
        members: list[SpaceMember] = []
        for row in rows:
            members.append(
                SpaceMember(
                    user_id=self._required_string(row, "id", "space_member_missing_id"),
                    username=self._required_string(row, "username", "space_member_missing_username"),
                    role_name=self._required_string(row, "role_name", "space_member_missing_role"),
                )
            )
        return tuple(members)

    def _list_paginated(self, path: str, bearer_token: str) -> tuple[dict[str, object], ...]:
        expected_total_pages: int | None = None
        page = 1
        rows: list[dict[str, object]] = []

        while expected_total_pages is None or page <= expected_total_pages:
            response = self._request_object(path, bearer_token, params={"limit": 100, "page": page})
            raw_rows = response.get("data")
            if not isinstance(raw_rows, list):
                raise UpstreamContractError("upstream_pagination_incomplete")
            current_page = response.get("current_page")
            total_pages = response.get("total_page")
            if (
                not isinstance(current_page, int)
                or current_page != page
                or not isinstance(total_pages, int)
                or total_pages < page
                or (expected_total_pages is not None and total_pages != expected_total_pages)
            ):
                raise UpstreamContractError("upstream_pagination_incomplete")
            expected_total_pages = total_pages
            for raw_row in raw_rows:
                rows.append(self._as_object(raw_row, "upstream_pagination_incomplete"))
            page += 1

        return tuple(rows)

    def _request_object(
        self,
        path: str,
        bearer_token: str,
        *,
        params: dict[str, int] | None = None,
    ) -> dict[str, object]:
        try:
            response = self._client.get(
                f"{self._base_url}{path}",
                headers={"Authorization": f"Bearer {bearer_token}"},
                params=params,
            )
        except httpx.RequestError as error:
            raise UpstreamUnavailableError("upstream_unavailable") from error

        if response.status_code == 401:
            raise AuthenticationError("upstream_unauthorized")
        if response.status_code == 403:
            raise AuthorizationError("upstream_forbidden")
        if response.status_code >= 500:
            raise UpstreamUnavailableError("upstream_unavailable")
        if not response.is_success:
            raise UpstreamContractError("upstream_request_rejected")
        try:
            payload: object = response.json()
        except ValueError as error:
            raise UpstreamContractError("upstream_invalid_json") from error

        object_payload = self._as_object(payload, "upstream_invalid_response")
        data = object_payload.get("data")
        if isinstance(data, dict):
            return self._as_object(data, "upstream_invalid_response")
        return object_payload

    @staticmethod
    def _as_object(value: object, error_code: str) -> dict[str, object]:
        if not isinstance(value, dict) or not all(isinstance(key, str) for key in value):
            raise UpstreamContractError(error_code)
        return {key: item for key, item in value.items()}

    @staticmethod
    def _required_string(payload: dict[str, object], key: str, error_code: str) -> str:
        value = payload.get(key)
        if not isinstance(value, str) or not value.strip():
            raise UpstreamContractError(error_code)
        return value
