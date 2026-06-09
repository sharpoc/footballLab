from worldcup.secrets import format_env_assignment, generate_hmac_secret


def test_generate_hmac_secret_defaults_to_32_random_bytes_as_hex():
    secret = generate_hmac_secret()

    assert len(secret) == 64
    int(secret, 16)


def test_format_env_assignment_uses_variable_name_without_printing_extra_context():
    assert format_env_assignment("abc123") == "INGEST_HMAC_SECRET=abc123"
