"""Real disposable PostgreSQL; external Supabase verification uses synthetic HTTP."""
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4
import psycopg
import pytest
from app.errors import PublicError
from app.modules.identity.bootstrap import bootstrap_first_admin
from tests.test_identity_auth import USER_ID, settings, token, verifier

pytestmark=pytest.mark.postgres


def test_bootstrap_atomic_event_and_duplicate(identity_client):
    client,key,url=identity_client
    factory=client.app.state.session_factory
    with factory() as session:
        bootstrap_first_admin(session,client.app.state.identity_verifier,USER_ID,token(key))
    with psycopg.connect(url) as db:
        assert db.execute('SELECT user_id FROM platform_administrators').fetchall()==[(USER_ID,)]
        assert db.execute('SELECT target_user_id FROM administrator_bootstrap').fetchall()==[(USER_ID,)]
    with factory() as session,pytest.raises(PublicError) as error:
        bootstrap_first_admin(session,client.app.state.identity_verifier,USER_ID,token(key))
    assert error.value.code=='ADMINISTRATOR_BOOTSTRAP_CLOSED'
from fastapi import Depends
from sqlalchemy import text
from app.modules.identity import bootstrap as command
from app.modules.identity.roles import require_platform_administrator, require_institution_moderator
from tests.test_identity_auth import user
from tests.test_identity_postgres import identity_client as base_identity_client

@pytest.fixture
def identity_client(base_identity_client):
    client,key,url=base_identity_client
    with psycopg.connect(url) as db:
        db.execute('DELETE FROM institution_moderators')
        db.execute('DELETE FROM platform_administrators')
        # Only this module's unique disposable database. Never an operator reset path.
        db.execute('TRUNCATE administrator_bootstrap')
        db.execute('DELETE FROM memberships')
        db.execute('DELETE FROM institutions')
        db.execute('DELETE FROM profiles')
    yield client,key,url


def counts(url):
    with psycopg.connect(url) as db:
        return tuple(db.execute(f'SELECT count(*) FROM {table}').fetchone()[0]
                     for table in ('profiles','platform_administrators','administrator_bootstrap'))


def auth(key,**claims):
    return {'Authorization':f'Bearer {token(key,**claims)}'}


def test_concurrent_bootstrap_single_grant_and_event(identity_client):
    client,key,url=identity_client
    def run(_):
        with client.app.state.session_factory() as session:
            try:
                bootstrap_first_admin(session,client.app.state.identity_verifier,USER_ID,token(key))
                return 'success'
            except PublicError as error:
                return error.code
    with ThreadPoolExecutor(max_workers=6) as pool:
        results=list(pool.map(run,range(6)))
    assert results.count('success')==1
    assert results.count('ADMINISTRATOR_BOOTSTRAP_CLOSED')==5
    assert counts(url)==(1,1,1)
    with psycopg.connect(url) as db:
        event=db.execute('SELECT target_user_id,occurred_at,actor,reason FROM administrator_bootstrap').fetchone()
        assert event[0]==USER_ID and event[1].tzinfo is not None
        assert event[2]=='operator_command' and event[3]=='Initial platform administrator bootstrap.'


def test_removed_grant_and_profile_do_not_reopen_bootstrap(identity_client):
    client,key,url=identity_client
    with client.app.state.session_factory() as session:
        bootstrap_first_admin(session,client.app.state.identity_verifier,USER_ID,token(key))
    with psycopg.connect(url) as db:
        db.execute('DELETE FROM platform_administrators')
        db.execute('DELETE FROM profiles')
    assert counts(url)==(0,0,1)
    with client.app.state.session_factory() as session,pytest.raises(PublicError) as error:
        bootstrap_first_admin(session,client.app.state.identity_verifier,USER_ID,token(key))
    assert error.value.code=='ADMINISTRATOR_BOOTSTRAP_CLOSED'
    assert counts(url)==(0,0,1)
    with psycopg.connect(url) as db:
        for query in ['DELETE FROM administrator_bootstrap',"UPDATE administrator_bootstrap SET actor='other'"]:
            with pytest.raises(psycopg.errors.RaiseException):db.execute(query)
            db.rollback()


def test_existing_admin_without_marker_refuses_bootstrap(identity_client):
    client,key,url=identity_client
    other=uuid4()
    with psycopg.connect(url) as db:
        db.execute('INSERT INTO profiles(id) VALUES (%s)',(other,))
        db.execute('INSERT INTO platform_administrators(user_id) VALUES (%s)',(other,))
    with client.app.state.session_factory() as session,pytest.raises(PublicError) as error:
        bootstrap_first_admin(session,client.app.state.identity_verifier,USER_ID,token(key))
    assert error.value.code=='ADMINISTRATOR_BOOTSTRAP_CLOSED'
    assert counts(url)==(1,1,0)


def test_bootstrap_refuses_unverified_nonexistent_mismatch_invalid_and_blocked(identity_client):
    client,key,url=identity_client
    factory=client.app.state.session_factory
    cases=[(verifier(key,user_data=user(email_confirmed_at=None)),USER_ID,token(key),'EMAIL_VERIFICATION_REQUIRED'),
           (verifier(key,status=404),USER_ID,token(key),'AUTH_UNAVAILABLE'),
           (verifier(key),uuid4(),token(key),'BOOTSTRAP_TARGET_MISMATCH'),
           (verifier(key),USER_ID,token(key,exp=1),'INVALID_CREDENTIALS'),
           (verifier(key),USER_ID,'invalid','INVALID_CREDENTIALS'),
           (verifier(key,user_data=user(is_anonymous=True)),USER_ID,token(key),'EMAIL_VERIFICATION_REQUIRED')]
    for check,target,encoded,code in cases:
        with check, factory() as session,pytest.raises(PublicError) as error:
            bootstrap_first_admin(session,check,target,encoded)
        assert error.value.code==code and counts(url)==(0,0,0)
    with psycopg.connect(url) as db:
        db.execute("INSERT INTO profiles(id,account_status) VALUES (%s,'blocked')",(USER_ID,))
    with factory() as session,pytest.raises(PublicError) as error:
        bootstrap_first_admin(session,client.app.state.identity_verifier,USER_ID,token(key))
    assert error.value.code=='ACCOUNT_BLOCKED' and counts(url)==(1,0,0)


def test_bootstrap_real_database_failure_rolls_back_all(identity_client):
    client,key,url=identity_client
    factory=client.app.state.session_factory
    class FailingSession(factory.class_):
        def commit(self):
            self.flush()
            self.execute(text('SELECT * FROM bootstrap_secret_missing_table'))
    with FailingSession(bind=factory.kw['bind']) as session,pytest.raises(PublicError) as error:
        bootstrap_first_admin(session,client.app.state.identity_verifier,USER_ID,token(key))
    assert error.value.code=='DATABASE_UNAVAILABLE'
    assert 'bootstrap_secret_missing_table' not in str(error.value) and url not in str(error.value)
    assert counts(url)==(0,0,0)
    # Failed attempt did not consume bootstrap; a valid isolated retry succeeds.
    with factory() as session:
        bootstrap_first_admin(session,client.app.state.identity_verifier,USER_ID,token(key))
    assert counts(url)==(1,1,1)


def test_current_administrator_checks_and_no_membership_bypass(identity_client):
    client,key,url=identity_client
    @client.app.get('/test-admin')
    def admin(grant=Depends(require_platform_administrator)):
        return {'authorized':True}
    path='/test-admin'
    r=client.get(path,headers=auth(key,role='service_role',user_metadata={'is_admin':True}))
    assert r.status_code==403 and r.json()['error']['code']=='PLATFORM_ADMINISTRATOR_REQUIRED'
    with client.app.state.session_factory() as session:
        bootstrap_first_admin(session,client.app.state.identity_verifier,USER_ID,token(key))
    headers=auth(key)
    assert client.get(path,headers=headers).status_code==200
    with psycopg.connect(url) as db:
        assert db.execute('SELECT count(*) FROM memberships').fetchone()[0]==0
        db.execute("UPDATE profiles SET account_status='blocked'")
    assert client.get(path,headers=headers).status_code==403
    with psycopg.connect(url) as db:
        db.execute("UPDATE profiles SET account_status='active'")
        db.execute('DELETE FROM platform_administrators')
    r=client.get(path,headers=headers)
    assert r.status_code==403 and r.json()['error']['requestId']==r.headers['X-Request-ID']
    assert counts(url)==(1,0,1)


def moderator_fixture(client,url):
    pilot=uuid4()
    client.app.state.pilot_institution_id=pilot
    with psycopg.connect(url) as db:
        db.execute('INSERT INTO profiles(id) VALUES (%s)',(USER_ID,))
        db.execute('INSERT INTO institutions(id) VALUES (%s)',(pilot,))
        db.execute("INSERT INTO memberships(institution_id,user_id,request_id,status) VALUES (%s,%s,%s,'active')",(pilot,USER_ID,uuid4()))
    @client.app.get('/test-moderator/{institution_id}')
    def moderator(grant=Depends(require_institution_moderator)):
        return {'institutionId':str(grant.institution_id)}
    return pilot


def test_moderator_current_role_membership_blocking_and_scoping(identity_client):
    client,key,url=identity_client
    pilot=moderator_fixture(client,url)
    path=f'/test-moderator/{pilot}'
    r=client.get(path,headers=auth(key,role='moderator',user_metadata={'role':'admin'}))
    assert r.status_code==403 and r.json()['error']['code']=='INSTITUTION_MODERATOR_REQUIRED'
    with psycopg.connect(url) as db:
        db.execute('INSERT INTO institution_moderators(institution_id,user_id) VALUES (%s,%s)',(pilot,USER_ID))
    headers=auth(key)
    assert client.get(path,headers=headers).status_code==200
    other=uuid4()
    with psycopg.connect(url) as db:
        db.execute('INSERT INTO institutions(id) VALUES (%s)',(other,))
        db.execute("INSERT INTO memberships(institution_id,user_id,request_id,status) VALUES (%s,%s,%s,'active')",(other,USER_ID,uuid4()))
    client.app.state.pilot_institution_id=other
    assert client.get(f'/test-moderator/{other}',headers=headers).status_code==403
    client.app.state.pilot_institution_id=pilot
    for state in ['pending','rejected','revoked']:
        with psycopg.connect(url) as db:
            db.execute('UPDATE memberships SET status=%s WHERE institution_id=%s',(state,pilot))
        assert client.get(path,headers=headers).status_code==403
    with psycopg.connect(url) as db:
        db.execute("UPDATE memberships SET status='active' WHERE institution_id=%s",(pilot,))
        db.execute("UPDATE profiles SET account_status='blocked'")
    assert client.get(path,headers=headers).status_code==403
    with psycopg.connect(url) as db:
        db.execute("UPDATE profiles SET account_status='active'")
        db.execute('DELETE FROM institution_moderators')
    assert client.get(path,headers=headers).status_code==403


def test_role_checks_deny_invalid_expired_and_unverified(identity_client):
    client,key,url=identity_client
    pilot=moderator_fixture(client,url)
    @client.app.get('/test-admin')
    def admin(grant=Depends(require_platform_administrator)):
        return {'ok':True}
    for path in ['/test-admin',f'/test-moderator/{pilot}']:
        for headers in [{},{'Authorization':'Bearer invalid'},auth(key,exp=1)]:
            assert client.get(path,headers=headers).status_code==401
    client.app.state.identity_verifier.close()
    client.app.state.identity_verifier=verifier(key,user_data=user(email_confirmed_at=None))
    for path in ['/test-admin',f'/test-moderator/{pilot}']:
        assert client.get(path,headers=auth(key)).status_code==403


def test_role_read_failures_are_sanitized(identity_client):
    client,key,url=identity_client
    pilot=moderator_fixture(client,url)
    @client.app.get('/test-admin')
    def admin(grant=Depends(require_platform_administrator)):
        return {'ok':True}
    for table,path in [('platform_administrators','/test-admin'),('institution_moderators',f'/test-moderator/{pilot}')]:
        with psycopg.connect(url) as db:db.execute(f'ALTER TABLE {table} RENAME TO secret_unavailable_table')
        try:
            r=client.get(path,headers=auth(key))
            assert r.status_code==503 and r.json()['error']['code']=='DATABASE_UNAVAILABLE'
            assert 'secret_unavailable_table' not in r.text and url not in r.text
        finally:
            with psycopg.connect(url) as db:db.execute(f'ALTER TABLE secret_unavailable_table RENAME TO {table}')


def test_operator_command_success_and_failure_do_not_echo_token(identity_client,monkeypatch,capsys):
    client,key,url=identity_client
    config=client.app.state.identity_verifier.settings.model_copy(update={
        'database_url':settings().database_url.__class__(url.replace('postgresql://','postgresql+psycopg://',1))})
    encoded=token(key)
    monkeypatch.setattr(command,'load_settings',lambda:config)
    monkeypatch.setattr(command,'getpass',lambda prompt:encoded)
    monkeypatch.setattr(command,'SupabaseVerifier',lambda _:verifier(key))
    assert command.main(['--user-id',str(USER_ID)])==0
    output=capsys.readouterr()
    assert 'bootstrapped' in output.out and encoded not in output.out+output.err and url not in output.out+output.err
    assert command.main(['--user-id',str(USER_ID)])==1
    output=capsys.readouterr()
    assert 'ADMINISTRATOR_BOOTSTRAP_CLOSED' in output.err
    assert encoded not in output.out+output.err and url not in output.out+output.err
    assert command.main(['--user-id','secret-invalid-uuid-marker'])==1
    output=capsys.readouterr()
    assert 'INVALID_OPERATOR_INPUT' in output.err and 'secret-invalid-uuid-marker' not in output.err


def test_hidden_input_fails_closed_without_echo_fallback(monkeypatch,capsys):
    monkeypatch.setattr(command,'load_settings',settings)
    def unavailable(prompt):raise command.GetPassWarning('secret-terminal-marker')
    monkeypatch.setattr(command,'getpass',unavailable)
    assert command.main(['--user-id',str(USER_ID)])==1
    output=capsys.readouterr()
    assert 'SECURE_INPUT_REQUIRED' in output.err and 'secret-terminal-marker' not in output.err


def test_role_tables_rls_singleton_and_foreign_keys(identity_client):
    client,key,url=identity_client
    with client.app.state.session_factory() as session:
        bootstrap_first_admin(session,client.app.state.identity_verifier,USER_ID,token(key))
    with psycopg.connect(url) as db:
        with pytest.raises(psycopg.errors.CheckViolation):
            db.execute("INSERT INTO administrator_bootstrap(singleton,target_user_id,actor,reason) VALUES (2,%s,'operator_command','invalid second marker')",(USER_ID,))
        db.rollback()
        with pytest.raises(psycopg.errors.ForeignKeyViolation):
            db.execute('INSERT INTO institution_moderators(institution_id,user_id) VALUES (%s,%s)',(uuid4(),USER_ID))
        db.rollback()
        db.execute('CREATE ROLE administration_browser NOLOGIN')
        db.execute('GRANT USAGE ON SCHEMA public TO administration_browser')
        db.execute('GRANT SELECT,INSERT,UPDATE,DELETE ON platform_administrators,institution_moderators,administrator_bootstrap TO administration_browser')
        db.execute('SET LOCAL ROLE administration_browser')
        for table in ('platform_administrators','institution_moderators','administrator_bootstrap'):
            assert db.execute(f'SELECT count(*) FROM {table}').fetchone()[0]==0
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            db.execute('INSERT INTO platform_administrators(user_id) VALUES (%s)',(USER_ID,))
        db.rollback()


def test_platform_admin_does_not_grant_membership_or_moderator_authority(identity_client):
    from app.modules.identity.membership import require_active_membership
    client,key,url=identity_client
    pilot=uuid4()
    client.app.state.pilot_institution_id=pilot
    with psycopg.connect(url) as db:db.execute('INSERT INTO institutions(id) VALUES (%s)',(pilot,))
    with client.app.state.session_factory() as session:
        bootstrap_first_admin(session,client.app.state.identity_verifier,USER_ID,token(key))
    @client.app.get('/test-content/{institution_id}')
    def content(member=Depends(require_active_membership)):
        return {'ok':True}
    @client.app.get('/test-moderator/{institution_id}')
    def moderator(grant=Depends(require_institution_moderator)):
        return {'ok':True}
    assert client.get(f'/test-content/{pilot}',headers=auth(key)).status_code==403
    assert client.get(f'/test-moderator/{pilot}',headers=auth(key)).status_code==403


def test_concurrent_different_verified_targets_have_one_first_administrator(identity_client):
    client,key,url=identity_client
    targets=[uuid4() for _ in range(6)]
    def run(subject):
        with verifier(key,user_data=user(id=str(subject))) as check, client.app.state.session_factory() as session:
            try:
                bootstrap_first_admin(session,check,subject,token(key,sub=str(subject)))
                return subject
            except PublicError as error:
                assert error.code=='ADMINISTRATOR_BOOTSTRAP_CLOSED'
                return None
    with ThreadPoolExecutor(max_workers=6) as pool:
        results=list(pool.map(run,targets))
    winners=[value for value in results if value is not None]
    assert len(winners)==1 and counts(url)==(1,1,1)
    with psycopg.connect(url) as db:
        assert db.execute('SELECT target_user_id FROM administrator_bootstrap').fetchone()[0]==winners[0]
        assert db.execute('SELECT user_id FROM platform_administrators').fetchone()[0]==winners[0]


def test_standalone_operator_import_resolves_all_foreign_keys():
    import subprocess
    import sys
    from pathlib import Path
    result=subprocess.run([sys.executable,'-c',
        'from app.modules.identity.bootstrap import bootstrap_first_admin; '
        'from app.db import Base; '
        'assert {t.name for t in Base.metadata.sorted_tables} == '
        '{"profiles","institutions","memberships","platform_administrators","institution_moderators","administrator_bootstrap"}'],
        cwd=Path(__file__).resolve().parents[1],capture_output=True)
    assert result.returncode==0, 'Standalone operator metadata could not resolve referenced tables.'
