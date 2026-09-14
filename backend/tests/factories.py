import uuid

PASSWORD = "correct horse battery"


def registration(**overrides):
    return {
        "first_name": "Test",
        "last_name": "User",
        "email": f"user-{uuid.uuid4().hex}@example.org",
        "password": PASSWORD,
        "position_role": "Researcher",
        "reason_for_joining": "testing",
        "institution": "Test University",
        **overrides,
    }
