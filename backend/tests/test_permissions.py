from permissions import ROLE_PERMISSIONS, Permission, ProjectRole, has_permission


def test_every_role_has_permissions_defined():
    assert set(ROLE_PERMISSIONS) == set(ProjectRole)


def test_every_role_can_view_the_project():
    assert all(has_permission(role, Permission.VIEW_PROJECT) for role in ProjectRole)


def test_owner_can_do_everything():
    assert ROLE_PERMISSIONS[ProjectRole.OWNER] == frozenset(Permission)


def test_only_owners_can_delete_projects():
    assert [role for role in ProjectRole if has_permission(role, Permission.DELETE_PROJECT)] == [ProjectRole.OWNER]


def test_viewer_is_read_only():
    assert ROLE_PERMISSIONS[ProjectRole.VIEWER] == {Permission.VIEW_PROJECT}


def test_auditor_can_review_but_not_change_the_review():
    auditor = ROLE_PERMISSIONS[ProjectRole.AUDITOR]

    assert Permission.VIEW_AUDIT in auditor
    assert not auditor & {Permission.SCREEN, Permission.EXTRACT, Permission.APPRAISE, Permission.EDIT_PROTOCOL}


def test_only_statistics_roles_approve_analyses():
    approvers = {role for role in ProjectRole if has_permission(role, Permission.APPROVE_ANALYSIS)}

    assert approvers == {ProjectRole.OWNER, ProjectRole.LEAD_REVIEWER, ProjectRole.STATISTICIAN}


def test_unknown_role_has_no_permissions():
    assert not has_permission("superuser", Permission.VIEW_PROJECT)
