"""Real disposable PostgreSQL and signed JWTs; Supabase HTTP responses are synthetic."""
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4
import psycopg
import pytest
from tests.test_identity_auth import token

pytestmark = pytest.mark.postgres

def test_concurrent_requests_have_one_pending(identity_client):
    client, key, url = identity_client
    institution = uuid4()
    client.app.state.pilot_institution_id = institution
    # The institution is provisioned explicitly in this isolated fixture only.
    with psycopg.connect(url) as db:
        db.execute('INSERT INTO institutions(id) VALUES (%s)', (institution,))
    path = f'/api/v1/institutions/{institution}/membership-requests'
    headers = {'Authorization': f'Bearer {token(key)}'}
    with ThreadPoolExecutor(max_workers=6) as pool:
        responses = list(pool.map(lambda _: client.post(path, json={}, headers=headers), range(6)))
    assert sorted(r.status_code for r in responses) == [201,409,409,409,409,409]
    with psycopg.connect(url) as db:
        assert db.execute('SELECT count(*) FROM memberships WHERE institution_id=%s AND status=\'pending\'', (institution,)).fetchone()[0] == 1
from fastapi import Depends
from sqlalchemy import text
from app.modules.identity.membership import require_active_membership
from tests.test_identity_auth import USER_ID, user, verifier
from tests.test_identity_postgres import identity_client as base_identity_client

@pytest.fixture
def identity_client(base_identity_client):
    client, key, url = base_identity_client
    with psycopg.connect(url) as db:
        db.execute('DELETE FROM memberships')
        db.execute('DELETE FROM institutions')
        db.execute('DELETE FROM profiles')
    yield client, key, url


def setup_pilot(client, url):
    institution = uuid4()
    client.app.state.pilot_institution_id = institution
    with psycopg.connect(url) as db:
        db.execute('INSERT INTO institutions(id) VALUES (%s)', (institution,))
    return institution


def auth(key, **claims):
    return {'Authorization': f'Bearer {token(key, **claims)}'}


def request_path(institution):
    return f'/api/v1/institutions/{institution}/membership-requests'


def snapshot(url):
    with psycopg.connect(url) as db:
        return (db.execute('SELECT * FROM profiles ORDER BY id').fetchall(),
                db.execute('SELECT * FROM memberships ORDER BY institution_id,user_id').fetchall())


def test_persistence_duplicate_and_owner_read(identity_client):
    client, key, url = identity_client
    pilot = setup_pilot(client, url)
    response = client.post(request_path(pilot), json={}, headers=auth(key, role='admin'))
    assert response.status_code == 201
    data = response.json()
    assert set(data) == {'requestId','institutionId','status','requestedAt'}
    assert data['status'] == 'pending' and data['institutionId'] == str(pilot)
    path = f"/api/v1/membership-requests/{data['requestId']}"
    assert client.get(path, headers=auth(key)).json() == data
    duplicate = client.post(request_path(pilot), json={}, headers=auth(key))
    assert duplicate.status_code == 409
    assert duplicate.json()['error']['code'] == 'MEMBERSHIP_REQUEST_PENDING'
    with psycopg.connect(url) as db:
        assert db.execute('SELECT user_id,status FROM memberships').fetchall() == [(USER_ID,'pending')]
    stranger = uuid4()
    client.app.state.identity_verifier.close()
    client.app.state.identity_verifier = verifier(key, user_data=user(id=str(stranger)))
    denied = client.get(path, headers=auth(key, sub=str(stranger)))
    assert denied.status_code == 404
    assert denied.json()['error']['code'] == 'MEMBERSHIP_REQUEST_NOT_FOUND'
    with psycopg.connect(url) as db:
        assert db.execute('SELECT count(*) FROM profiles WHERE id=%s',(stranger,)).fetchone()[0] == 0


def test_rejected_rerequest_active_conflict_and_revoked_no_recovery(identity_client):
    client,key,url=identity_client
    pilot=setup_pilot(client,url)
    first=client.post(request_path(pilot),json={},headers=auth(key)).json()
    with psycopg.connect(url) as db:
        db.execute("UPDATE memberships SET status='rejected'")
    assert client.get(f"/api/v1/membership-requests/{first['requestId']}",headers=auth(key)).json()['status']=='rejected'
    again=client.post(request_path(pilot),json={},headers=auth(key))
    assert again.status_code==201 and again.json()['status']=='pending'
    assert again.json()['requestId']!=first['requestId']
    assert client.get(f"/api/v1/membership-requests/{first['requestId']}",headers=auth(key)).status_code==404
    for state,code in [('active','MEMBERSHIP_ALREADY_ACTIVE'),('revoked','MEMBERSHIP_REINSTATEMENT_REQUIRED')]:
        with psycopg.connect(url) as db:
            db.execute('UPDATE memberships SET status=%s',(state,))
        before=snapshot(url)
        r=client.post(request_path(pilot),json={},headers=auth(key))
        assert r.status_code==409 and r.json()['error']['code']==code
        assert snapshot(url)==before


def test_invalid_payload_and_institution_never_write(identity_client):
    client,key,url=identity_client
    pilot=setup_pilot(client,url)
    for field in ['userId','user_id','status','role','roles','reviewerId','approvedBy','institutionId','contributorStatus','isAdmin','unknown']:
        before=snapshot(url)
        r=client.post(request_path(pilot),json={field:'secret-input-marker'},headers=auth(key))
        assert r.status_code==422 and r.json()['error']['code']=='VALIDATION_ERROR'
        assert 'secret-input-marker' not in r.text
        assert snapshot(url)==before
    for body in [None, [], '', {'status':'pending','userId':str(uuid4())}]:
        assert client.post(request_path(pilot),json=body,headers=auth(key)).status_code==422
    assert snapshot(url)==([],[])
    other=uuid4()
    with psycopg.connect(url) as db:
        db.execute('INSERT INTO institutions(id) VALUES (%s)',(other,))
    for identifier in [other,uuid4()]:
        assert client.post(request_path(identifier),json={},headers=auth(key)).status_code==404
    with psycopg.connect(url) as db:
        db.execute('DELETE FROM institutions WHERE id=%s',(pilot,))
    assert client.post(request_path(pilot),json={},headers=auth(key)).status_code==404
    client.app.state.pilot_institution_id=None
    r=client.post(request_path(pilot),json={},headers=auth(key))
    assert r.status_code==503 and r.json()['error']['code']=='MEMBERSHIP_UNAVAILABLE'
    assert snapshot(url)==([],[])


def test_auth_blocking_and_identity_override(identity_client):
    client,key,url=identity_client
    pilot=setup_pilot(client,url)
    for header in [{}, {'Authorization':'Bearer invalid'}, auth(key,exp=1)]:
        assert client.post(request_path(pilot),json={},headers=header).status_code==401
    client.app.state.identity_verifier.close()
    client.app.state.identity_verifier=verifier(key,user_data=user(email_confirmed_at=None))
    assert client.post(request_path(pilot),json={},headers=auth(key)).status_code==403
    assert snapshot(url)==([],[])
    client.app.state.identity_verifier.close()
    client.app.state.identity_verifier=verifier(key)
    with psycopg.connect(url) as db:
        db.execute("INSERT INTO profiles(id,account_status) VALUES (%s,'blocked')",(USER_ID,))
    before=snapshot(url)
    assert client.post(request_path(pilot),json={},headers=auth(key)).status_code==403
    assert snapshot(url)==before
    with psycopg.connect(url) as db:
        db.execute("UPDATE profiles SET account_status='active'")
    r=client.post(request_path(pilot)+f'?userId={uuid4()}&role=admin',json={},headers=auth(key))
    assert r.status_code==201
    with psycopg.connect(url) as db:
        assert db.execute('SELECT user_id FROM memberships').fetchall()==[(USER_ID,)]
    with psycopg.connect(url) as db:
        db.execute("UPDATE profiles SET account_status='blocked'")
    assert client.get(f"/api/v1/membership-requests/{r.json()['requestId']}",headers=auth(key)).status_code==403


def test_active_dependency_current_state_and_institution_isolation(identity_client):
    client,key,url=identity_client
    pilot=setup_pilot(client,url)
    @client.app.get('/test-institutions/{institution_id}/protected')
    def protected(membership=Depends(require_active_membership)):
        return {'institutionId':str(membership.institution_id)}
    path=f'/test-institutions/{pilot}/protected'
    assert client.get(path,headers=auth(key)).status_code==403
    client.post(request_path(pilot),json={},headers=auth(key))
    for state in ['pending','rejected','revoked']:
        with psycopg.connect(url) as db:
            db.execute('UPDATE memberships SET status=%s',(state,))
        r=client.get(path,headers=auth(key))
        assert r.status_code==403 and r.json()['error']['code']=='ACTIVE_MEMBERSHIP_REQUIRED'
    with psycopg.connect(url) as db:
        db.execute("UPDATE memberships SET status='active'")
    assert client.get(path,headers=auth(key)).status_code==200
    other=uuid4()
    with psycopg.connect(url) as db:
        db.execute('INSERT INTO institutions(id) VALUES (%s)',(other,))
    assert client.get(f'/test-institutions/{other}/protected',headers=auth(key)).status_code==404
    client.app.state.pilot_institution_id=other
    assert client.get(f'/test-institutions/{other}/protected',headers=auth(key)).status_code==403
    client.app.state.pilot_institution_id=pilot
    # Same unexpired token, separate subsequent request, current committed revocation.
    with psycopg.connect(url) as db:
        db.execute("UPDATE memberships SET status='revoked'")
    assert client.get(path,headers=auth(key)).status_code==403


def test_real_database_failure_rolls_back_request_and_profile(identity_client):
    client,key,url=identity_client
    pilot=setup_pilot(client,url)
    factory=client.app.state.session_factory
    class FailingSession(factory.class_):
        def commit(self):
            self.flush()
            self.execute(text('SELECT * FROM membership_missing_secret_table'))
    client.app.state.session_factory=lambda:FailingSession(bind=factory.kw['bind'])
    r=client.post(request_path(pilot),json={},headers=auth(key))
    assert r.status_code==503 and r.json()['error']['code']=='DATABASE_UNAVAILABLE'
    assert 'membership_missing_secret_table' not in r.text and url not in r.text
    assert r.headers['X-Request-ID']==r.json()['error']['requestId']
    assert snapshot(url)==([],[])
    client.app.state.session_factory=factory
    assert client.post(request_path(pilot),json={},headers=auth(key)).status_code==201
    with psycopg.connect(url) as db:
        db.execute("UPDATE memberships SET status='rejected'")
    before=snapshot(url)
    client.app.state.session_factory=lambda:FailingSession(bind=factory.kw['bind'])
    assert client.post(request_path(pilot),json={},headers=auth(key)).status_code==503
    assert snapshot(url)==before
    request_id=before[1][0][2]
    assert client.get(f'/api/v1/membership-requests/{request_id}',headers=auth(key)).status_code==503


def test_membership_constraints_and_rls(identity_client):
    client,key,url=identity_client
    pilot=setup_pilot(client,url)
    client.post(request_path(pilot),json={},headers=auth(key))
    with psycopg.connect(url) as db:
        with pytest.raises(psycopg.errors.UniqueViolation):
            db.execute('INSERT INTO memberships(institution_id,user_id,request_id) VALUES (%s,%s,%s)',(pilot,USER_ID,uuid4()))
        db.rollback()
        with pytest.raises(psycopg.errors.CheckViolation):
            db.execute("UPDATE memberships SET status='admin'")
        db.rollback()
        db.execute('CREATE ROLE membership_browser NOLOGIN')
        db.execute('GRANT USAGE ON SCHEMA public TO membership_browser')
        db.execute('GRANT SELECT,INSERT,UPDATE,DELETE ON institutions,memberships TO membership_browser')
        db.execute('SET LOCAL ROLE membership_browser')
        assert db.execute('SELECT count(*) FROM memberships').fetchone()[0]==0
        assert db.execute('SELECT count(*) FROM institutions').fetchone()[0]==0
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            db.execute('INSERT INTO institutions(id) VALUES (%s)',(uuid4(),))
        db.rollback()


def test_membership_openapi(identity_client):
    client,_,_=identity_client
    schema=client.get('/api/v1/openapi.json').json()
    route=schema['paths']['/api/v1/institutions/{institution_id}/membership-requests']['post']
    assert route['security']==[{'HTTPBearer':[]}]
    assert set(route['responses'])=={'201','401','403','404','409','422','503'}
    assert schema['components']['schemas']['EmptyRequest']['additionalProperties'] is False
    assert schema['components']['schemas']['EmptyRequest']['properties']=={}
    assert route['responses']['422']['content']['application/json']['schema']['$ref'].endswith('/ErrorResponse')


def test_late_block_and_database_read_failure(identity_client):
    from app.modules.identity.router import get_current_profile
    from app.modules.identity.auth import get_verified_subject
    from app.db import get_session
    client,key,url=identity_client
    pilot=setup_pilot(client,url)
    assert client.get('/api/v1/me',headers=auth(key)).status_code==200
    def block_after_lookup(subject=Depends(get_verified_subject),session=Depends(get_session)):
        profile=get_current_profile(subject,session)
        with psycopg.connect(url) as db:
            db.execute("UPDATE profiles SET account_status='blocked' WHERE id=%s",(subject,))
        return profile
    client.app.dependency_overrides[get_current_profile]=block_after_lookup
    assert client.post(request_path(pilot),json={},headers=auth(key)).status_code==403
    with psycopg.connect(url) as db:
        assert db.execute('SELECT count(*) FROM memberships').fetchone()[0]==0
        db.execute("UPDATE profiles SET account_status='active'")
    client.app.dependency_overrides.clear()
    created=client.post(request_path(pilot),json={},headers=auth(key)).json()
    @client.app.get('/test-read/{institution_id}')
    def protected(membership=Depends(require_active_membership)):
        return {'ok':True}
    with psycopg.connect(url) as db:
        db.execute('ALTER TABLE memberships RENAME TO unavailable_memberships')
    try:
        for method,path in [('post',request_path(pilot)),('get',f"/api/v1/membership-requests/{created['requestId']}"),('get',f'/test-read/{pilot}')]:
            r=getattr(client,method)(path,headers=auth(key),**({'json':{}} if method=='post' else {}))
            assert r.status_code==503 and r.json()['error']['code']=='DATABASE_UNAVAILABLE'
            assert 'unavailable_memberships' not in r.text and url not in r.text
    finally:
        with psycopg.connect(url) as db:
            db.execute('ALTER TABLE unavailable_memberships RENAME TO memberships')


def test_concurrent_rejected_rerequests_create_one_new_pending(identity_client):
    client,key,url=identity_client
    pilot=setup_pilot(client,url)
    first=client.post(request_path(pilot),json={},headers=auth(key)).json()
    with psycopg.connect(url) as db:
        db.execute("UPDATE memberships SET status='rejected'")
    with ThreadPoolExecutor(max_workers=6) as pool:
        responses=list(pool.map(lambda _:client.post(request_path(pilot),json={},headers=auth(key)),range(6)))
    assert sorted(r.status_code for r in responses)==[201,409,409,409,409,409]
    created=next(r.json() for r in responses if r.status_code==201)
    assert created['requestId']!=first['requestId']
    with psycopg.connect(url) as db:
        assert db.execute('SELECT count(*) FROM memberships').fetchone()[0]==1
        assert db.execute('SELECT status FROM memberships').fetchone()[0]=='pending'


def test_status_read_denies_invalid_expired_and_unverified_identity(identity_client):
    client,key,url=identity_client
    pilot=setup_pilot(client,url)
    created=client.post(request_path(pilot),json={},headers=auth(key)).json()
    path=f"/api/v1/membership-requests/{created['requestId']}"
    before=snapshot(url)
    for header in [{},{'Authorization':'Bearer invalid'},auth(key,exp=1)]:
        assert client.get(path,headers=header).status_code==401
    client.app.state.identity_verifier.close()
    client.app.state.identity_verifier=verifier(key,user_data=user(email_confirmed_at=None))
    assert client.get(path,headers=auth(key)).status_code==403
    assert snapshot(url)==before
