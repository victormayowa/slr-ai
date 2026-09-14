"""Project roles and what each role may do. This matrix is the single source of truth for authorization."""

from enum import StrEnum


class ProjectRole(StrEnum):
    OWNER = "owner"
    LEAD_REVIEWER = "lead_reviewer"
    METHODOLOGIST = "methodologist"
    STATISTICIAN = "statistician"
    CLINICAL_EXPERT = "clinical_expert"
    SCREENER = "screener"
    EXTRACTOR = "extractor"
    AUDITOR = "auditor"
    VIEWER = "viewer"


class Permission(StrEnum):
    VIEW_PROJECT = "project:view"
    EDIT_PROJECT = "project:edit"
    DELETE_PROJECT = "project:delete"
    MANAGE_MEMBERS = "members:manage"
    EDIT_PROTOCOL = "protocol:edit"
    APPROVE_PROTOCOL = "protocol:approve"
    RUN_SEARCH = "search:run"
    SCREEN = "screening:decide"
    ADJUDICATE = "screening:adjudicate"
    EXTRACT = "extraction:edit"
    APPRAISE = "appraisal:edit"
    RUN_ANALYSIS = "analysis:run"
    APPROVE_ANALYSIS = "analysis:approve"
    APPROVE_CERTAINTY = "certainty:approve"
    EDIT_MANUSCRIPT = "manuscript:edit"
    VIEW_AUDIT = "audit:view"
    EXPORT = "export:run"


_P = Permission

ROLE_PERMISSIONS: dict[ProjectRole, frozenset[Permission]] = {
    ProjectRole.OWNER: frozenset(Permission),
    ProjectRole.LEAD_REVIEWER: frozenset(Permission) - {_P.DELETE_PROJECT},
    ProjectRole.METHODOLOGIST: frozenset(
        {
            _P.VIEW_PROJECT,
            _P.EDIT_PROTOCOL,
            _P.APPROVE_PROTOCOL,
            _P.RUN_SEARCH,
            _P.SCREEN,
            _P.ADJUDICATE,
            _P.EXTRACT,
            _P.APPRAISE,
            _P.RUN_ANALYSIS,
            _P.APPROVE_CERTAINTY,
            _P.EDIT_MANUSCRIPT,
            _P.VIEW_AUDIT,
            _P.EXPORT,
        }
    ),
    ProjectRole.STATISTICIAN: frozenset(
        {_P.VIEW_PROJECT, _P.EXTRACT, _P.RUN_ANALYSIS, _P.APPROVE_ANALYSIS, _P.EDIT_MANUSCRIPT, _P.EXPORT}
    ),
    ProjectRole.CLINICAL_EXPERT: frozenset({_P.VIEW_PROJECT, _P.SCREEN, _P.EXTRACT, _P.APPRAISE, _P.EDIT_MANUSCRIPT}),
    ProjectRole.SCREENER: frozenset({_P.VIEW_PROJECT, _P.SCREEN}),
    ProjectRole.EXTRACTOR: frozenset({_P.VIEW_PROJECT, _P.EXTRACT, _P.APPRAISE}),
    ProjectRole.AUDITOR: frozenset({_P.VIEW_PROJECT, _P.VIEW_AUDIT, _P.EXPORT}),
    ProjectRole.VIEWER: frozenset({_P.VIEW_PROJECT}),
}


def has_permission(role: str, permission: Permission) -> bool:
    try:
        return permission in ROLE_PERMISSIONS[ProjectRole(role)]
    except ValueError:
        return False
