import logging
import psycopg
import pytest
from server.cloud_errors import describe_failure


@pytest.mark.parametrize('error,code', [
    (psycopg.OperationalError('network is unreachable password=TOPSECRET'), 'DATABASE_CONNECTION_FAILED'),
    (psycopg.OperationalError('password authentication failed TOPSECRET'), 'DATABASE_AUTH_FAILED'),
    (psycopg.errors.UndefinedTable('TOPSECRET'), 'DATABASE_SCHEMA_MISSING'),
    (psycopg.errors.InsufficientPrivilege('TOPSECRET'), 'DATABASE_PERMISSION_DENIED'),
    (KeyError('SUPABASE_PUBLISHABLE_KEY'), 'CONFIG_MISSING'),
    (RuntimeError('TOPSECRET'), 'CLOUD_SERVICE_ERROR'),
])
def test_errors_are_actionable_without_credentials(error, code, caplog):
    with caplog.at_level(logging.ERROR):
        result = describe_failure(error)
    assert result['code'] == code
    assert result['reference'] in caplog.text
    assert 'TOPSECRET' not in str(result) + caplog.text
