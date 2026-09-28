import pytest
from server.cloud_config import database_url


@pytest.mark.parametrize('host,expected', [
    ('[db.example.supabase.co]', 'db.example.supabase.co'),
    ('db.example.supabase.co', 'db.example.supabase.co'),
    ('[2001:db8::1]', '[2001:db8::1]'),
])
def test_database_host_normalization_preserves_password(monkeypatch, host, expected):
    monkeypatch.setenv('SUPABASE_DB_URL', f'postgresql://postgres:p%40ss%3Aword@{host}:5432/postgres?sslmode=require')
    assert database_url() == f'postgresql://postgres:p%40ss%3Aword@{expected}:5432/postgres?sslmode=require'


@pytest.mark.parametrize('value', ['https://host', 'postgresql://user:secret@[bad:address]/db', 'postgresql://user:secret@host:6543/db'])
def test_invalid_configuration_is_safe_and_actionable(monkeypatch, value):
    monkeypatch.setenv('SUPABASE_DB_URL', value)
    with pytest.raises(ValueError) as error:
        database_url()
    assert 'secret' not in str(error.value)
