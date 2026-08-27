from collections.abc import Callable

import httpx
import pytest

from classroom_sync.auth.fincolab import FincolabIdentityGateway, Principal, StudentChildExperiment
from classroom_sync.auth.student_binding import StudentBindingV1, encode_student_binding_v1
from classroom_sync.errors import (
    AuthenticationError,
    AuthorizationError,
    RosterConflictError,
    UpstreamContractError,
)

JsonResponder = Callable[[httpx.Request], httpx.Response]


def gateway(responder: JsonResponder) -> FincolabIdentityGateway:
    return FincolabIdentityGateway(
        base_url="https://fincolab.example",
        organization_id="org-1",
        client=httpx.Client(transport=httpx.MockTransport(responder)),
    )


def user_response(username: str, user_id: str, platform_role: str = "student") -> dict[str, str]:
    return {"id": user_id, "username": username, "platform_role": platform_role}


def member_response(*members: dict[str, object], total_page: int = 1) -> dict[str, object]:
    return {"data": list(members), "current_page": 1, "total_page": total_page}


def binding_description(*, space_id: str = "space-1", parent_id: str = "parent-1", username: str = "student-a") -> str:
    payload = encode_student_binding_v1(StudentBindingV1(space_id, parent_id, "student-1", username))
    return f"[FINCOLAB_PARENT_PROJECT_ID:{parent_id}][FINCOLAB_STUDENT_BINDING_V1:{payload}]"


def test_student_cannot_forge_teacher_role_from_client_claim():
    """Only the upstream space roster can establish the teacher role."""

    def responder(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v1/user/info":
            return httpx.Response(200, json=user_response("student-a", "student-1", "teacher"))
        if request.url.path == "/v1/organizations/org-1/spaces/space-1/users":
            return httpx.Response(
                200,
                json=member_response(
                    {"id": "student-1", "username": "student-a", "role_name": "student"}
                ),
            )
        raise AssertionError(f"Unexpected upstream request: {request.url}")

    identity_gateway = gateway(responder)
    principal = identity_gateway.resolve_principal("bearer-token")

    with pytest.raises(AuthorizationError, match="teacher_role_required"):
        identity_gateway.require_teacher_owner(principal, "space-1", "parent-1")


def test_teacher_cannot_publish_someone_elses_parent_experiment():
    """Parent experiment ownership is checked server-side before publication."""

    def responder(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v1/user/info":
            return httpx.Response(200, json=user_response("teacher-a", "teacher-1", "teacher"))
        if request.url.path == "/v1/organizations/org-1/spaces/space-1/users":
            return httpx.Response(
                200,
                json=member_response(
                    {"id": "teacher-1", "username": "teacher-a", "role_name": "teacher"}
                ),
            )
        if request.url.path == "/v1/spaces/space-1/algorithm_development/parent-1":
            return httpx.Response(200, json={"id": "parent-1", "username": "teacher-b"})
        raise AssertionError(f"Unexpected upstream request: {request.url}")

    identity_gateway = gateway(responder)
    principal = identity_gateway.resolve_principal("bearer-token")

    with pytest.raises(AuthorizationError, match="experiment_owner_mismatch"):
        identity_gateway.require_teacher_owner(principal, "space-1", "parent-1")


def test_missing_parent_owner_is_denied_instead_of_guessed():
    """An incomplete upstream owner response must never grant teacher access."""

    def responder(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v1/user/info":
            return httpx.Response(200, json=user_response("teacher-a", "teacher-1", "teacher"))
        if request.url.path == "/v1/organizations/org-1/spaces/space-1/users":
            return httpx.Response(
                200,
                json=member_response(
                    {"id": "teacher-1", "username": "teacher-a", "role_name": "teacher"}
                ),
            )
        if request.url.path == "/v1/spaces/space-1/algorithm_development/parent-1":
            return httpx.Response(200, json={"id": "parent-1"})
        raise AssertionError(f"Unexpected upstream request: {request.url}")

    identity_gateway = gateway(responder)
    principal = identity_gateway.resolve_principal("bearer-token")

    with pytest.raises(AuthorizationError, match="experiment_owner_unverified"):
        identity_gateway.require_teacher_owner(principal, "space-1", "parent-1")


def test_student_membership_reads_all_pages_before_authorizing():
    """A student found only on a later page remains eligible for their assignment."""
    requested_pages: list[str] = []

    def responder(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v1/user/info":
            return httpx.Response(200, json=user_response("student-a", "student-1"))
        if request.url.path == "/v1/organizations/org-1/spaces/space-1/users":
            page = request.url.params["page"]
            requested_pages.append(page)
            if page == "1":
                return httpx.Response(
                    200,
                    json={
                        "data": [{"id": "student-0", "username": "student-0", "role_name": "student"}],
                        "current_page": 1,
                        "total_page": 2,
                    },
                )
            return httpx.Response(
                200,
                json={
                    "data": [{"id": "student-1", "username": "student-a", "role_name": "student"}],
                    "current_page": 2,
                    "total_page": 2,
                },
            )
        raise AssertionError(f"Unexpected upstream request: {request.url}")

    identity_gateway = gateway(responder)
    principal = identity_gateway.resolve_principal("bearer-token")

    identity_gateway.require_student_member(principal, "space-1")

    assert requested_pages == ["1", "2"]


def test_upstream_unauthorized_is_mapped_to_a_client_authentication_error():
    identity_gateway = gateway(lambda _: httpx.Response(401, json={"detail": "expired"}))

    with pytest.raises(AuthenticationError, match="upstream_unauthorized"):
        identity_gateway.resolve_principal("expired-token")


def test_incomplete_pagination_is_treated_as_an_unavailable_authority_source():
    def responder(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v1/user/info":
            return httpx.Response(200, json=user_response("student-a", "student-1"))
        if request.url.path == "/v1/organizations/org-1/spaces/space-1/users":
            if request.url.params["page"] == "1":
                return httpx.Response(
                    200,
                    json={
                        "data": [{"id": "student-0", "username": "student-0", "role_name": "student"}],
                        "current_page": 1,
                        "total_page": 2,
                    },
                )
            return httpx.Response(200, json={"current_page": 2, "total_page": 2})
        raise AssertionError(f"Unexpected upstream request: {request.url}")

    identity_gateway = gateway(responder)
    principal = identity_gateway.resolve_principal("bearer-token")

    with pytest.raises(UpstreamContractError, match="upstream_pagination_incomplete"):
        identity_gateway.require_student_member(principal, "space-1")


def test_duplicate_child_projects_are_quarantined_instead_of_auto_assigned():
    """Two children for one student/parent pair cannot silently choose an environment."""

    def responder(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v1/user/info":
            return httpx.Response(200, json=user_response("teacher-a", "teacher-1", "teacher"))
        if request.url.path == "/v1/organizations/org-1/spaces/space-1/users":
            return httpx.Response(
                200,
                json=member_response(
                    {"id": "teacher-1", "username": "teacher-a", "role_name": "teacher"},
                    {"id": "student-1", "username": "student-a", "role_name": "student"},
                ),
            )
        if request.url.path == "/v1/spaces/space-1/algorithm_development":
            return httpx.Response(
                200,
                json={
                    "data": [
                        {
                            "id": "child-1",
                            "name": "exp-student-a-a1b2",
                            "username": "student-a",
                            "description": "[FINCOLAB_PARENT_PROJECT_ID:parent-1]",
                            "workbench_id": "workbench-1",
                        },
                        {
                            "id": "child-2",
                            "name": "exp-student-a-a1b3",
                            "username": "student-a",
                            "description": "[FINCOLAB_PARENT_PROJECT_ID:parent-1]",
                            "workbench_id": "workbench-2",
                        },
                    ],
                    "current_page": 1,
                    "total_page": 1,
                },
            )
        raise AssertionError(f"Unexpected upstream request: {request.url}")

    identity_gateway = gateway(responder)
    principal = identity_gateway.resolve_principal("bearer-token")

    with pytest.raises(RosterConflictError, match="duplicate_student_child"):
        identity_gateway.list_student_children(principal, "space-1", "parent-1")


def test_teacher_owned_v1_child_binds_the_marker_student_not_its_owner():
    def responder(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/users"):
            return httpx.Response(200, json=member_response(
                {"id": "teacher-1", "username": "teacher-a", "role_name": "teacher"},
                {"id": "student-1", "username": "student-a", "role_name": "student"},
            ))
        if request.url.path.endswith("/algorithm_development"):
            return httpx.Response(200, json=member_response({
                "id": "child-1", "username": "teacher-a", "description": binding_description(), "workbench_id": "workbench-1"
            }))
        raise AssertionError(request.url)

    roster = gateway(responder).list_student_children(Principal("teacher-1", "teacher-a", "token"), "space-1", "parent-1")

    assert roster == (StudentChildExperiment("student-1", "student-a", "child-1", "workbench-1"),)


@pytest.mark.parametrize(
    ("description", "code"),
    [
        (binding_description(space_id="space-2"), "student_binding_space_mismatch"),
        (
            "[FINCOLAB_PARENT_PROJECT_ID:parent-1][FINCOLAB_STUDENT_BINDING_V1:"
            + encode_student_binding_v1(StudentBindingV1("space-1", "parent-2", "student-1", "student-a"))
            + "]",
            "student_binding_parent_mismatch",
        ),
        (binding_description(username="student-b"), "student_binding_username_mismatch"),
        ("[FINCOLAB_PARENT_PROJECT_ID:parent-1][FINCOLAB_STUDENT_BINDING_V1:bad=]", "student_binding_marker_malformed"),
    ],
)
def test_current_parent_v1_marker_conflicts_fail_closed(description: str, code: str):
    def responder(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/users"):
            return httpx.Response(200, json=member_response({"id":"student-1","username":"student-a","role_name":"student"}))
        return httpx.Response(200, json=member_response({"id":"child-1","username":"teacher-a","description":description,"workbench_id":"workbench-1"}))

    with pytest.raises(RosterConflictError, match=code):
        gateway(responder).list_student_children(Principal("teacher-1", "teacher-a", "token"), "space-1", "parent-1")


def test_other_parent_malformed_marker_is_not_a_current_parent_candidate():
    def responder(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/users"):
            return httpx.Response(200, json=member_response({"id":"student-1","username":"student-a","role_name":"student"}))
        return httpx.Response(200, json=member_response({"id":"child-1","description":"[FINCOLAB_PARENT_PROJECT_ID:other][FINCOLAB_STUDENT_BINDING_V1:bad=]"}))

    assert gateway(responder).list_student_children(Principal("teacher-1", "teacher-a", "token"), "space-1", "parent-1") == ()
