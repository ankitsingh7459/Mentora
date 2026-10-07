"""Disposable PostgreSQL and locally signed JWTs; provider HTTP remains synthetic."""
import psycopg
import pytest
from tests.test_identity_postgres import identity_client as base_identity_client
from tests.test_membership_admin import seed,auth

pytestmark=pytest.mark.postgres


def test_revoke_and_reinstate_preserve_membership(identity_client):
    client,key,url=identity_client
    institution,target,request_id=seed(client,url)
    with psycopg.connect(url) as db:db.execute("UPDATE memberships SET status='active' WHERE user_id=%s",(target,))
    endpoint=f'/api/v1/institutions/{institution}/memberships/{target}'
    response=client.post(endpoint+'/revoke',json={'reason':'  Eligibility suspended  ','expectedVersion':0},headers=auth(key))
    assert response.status_code==200
    assert response.json()['status']=='revoked' and response.json()['version']==1
    response=client.post(endpoint+'/reinstate',json={'reason':'Eligibility restored','expectedVersion':1},headers=auth(key))
    assert response.status_code==200
    assert response.json()['status']=='active' and response.json()['version']==2
from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack
from uuid import uuid4
from fastapi import Depends
from fastapi.testclient import TestClient
from sqlalchemy import text
from app.main import create_app
from app.modules.identity.roles import require_institution_moderator
from app.modules.identity.membership_admin import require_membership_reviewer
from tests.test_identity_auth import USER_ID,user,verifier
from tests.test_membership_admin import identity_client as review_identity_client,switch_user,protected_route

@pytest.fixture
def identity_client(review_identity_client):
    client,key,url=review_identity_client
    with psycopg.connect(url) as db:db.execute('TRUNCATE membership_transitions')
    yield client,key,url


def endpoint(institution,target):return f'/api/v1/institutions/{institution}/memberships/{target}'


def active_seed(client,url,role='administrator'):
    institution,target,request_id=seed(client,url,role)
    with psycopg.connect(url) as db:db.execute("UPDATE memberships SET status='active' WHERE user_id=%s",(target,))
    return institution,target,request_id


def change(client,key,institution,target,operation,version,reason='Membership eligibility reviewed'):
    return client.post(endpoint(institution,target)+'/'+operation,
                       json={'reason':reason,'expectedVersion':version},headers=auth(key))


def stored(url,target):
    with psycopg.connect(url) as db:
        return (db.execute('SELECT request_id,status,transition_version FROM memberships WHERE user_id=%s ORDER BY institution_id',(target,)).fetchall(),
                db.execute('SELECT institution_id,user_id,granted_at FROM institution_moderators WHERE user_id=%s ORDER BY institution_id',(target,)).fetchall(),
                db.execute('SELECT * FROM membership_transitions ORDER BY version').fetchall(),
                db.execute('SELECT * FROM profiles ORDER BY id').fetchall())


def test_revoke_denies_same_token_reinstate_is_student_only_preserves_history(identity_client):
    client,key,url=identity_client
    institution,target,request_id=seed(client,url)
    assert client.post(f'/api/v1/institutions/{institution}/membership-requests/{request_id}/decision',json={'decision':'approve'},headers=auth(key)).status_code==200
    with psycopg.connect(url) as db:
        db.execute('INSERT INTO institution_moderators(institution_id,user_id) VALUES (%s,%s)',(institution,target))
        grant_time=db.execute('SELECT granted_at FROM institution_moderators WHERE user_id=%s',(target,)).fetchone()[0]
        db.execute("UPDATE profiles SET display_name='Preserved name' WHERE id=%s",(target,))
        other=uuid4()
        db.execute('INSERT INTO institutions(id) VALUES (%s)',(other,))
        db.execute("INSERT INTO memberships(institution_id,user_id,request_id,status) VALUES (%s,%s,%s,'active')",(other,target,uuid4()))
        db.execute('INSERT INTO institution_moderators(institution_id,user_id) VALUES (%s,%s)',(other,target))
        original_review=db.execute('SELECT * FROM membership_reviews').fetchall()
    protected_route(client)
    @client.app.get('/test-mod/{institution_id}')
    def mod(grant=Depends(require_institution_moderator)):return {'ok':True}
    target_headers=auth(key,target)
    switch_user(client,key,target)
    assert client.get(f'/test-access/{institution}',headers=target_headers).status_code==200
    assert client.get(f'/test-mod/{institution}',headers=target_headers).status_code==200
    switch_user(client,key,USER_ID)
    response=change(client,key,institution,target,'revoke',0)
    assert response.status_code==200 and response.json()['transition']['moderatorGrantInvalidated'] is True
    with psycopg.connect(url) as db:
        assert db.execute('SELECT moderator_granted_at FROM membership_transitions').fetchone()[0]==grant_time
        assert db.execute('SELECT institution_id FROM institution_moderators WHERE user_id=%s',(target,)).fetchall()==[(other,)]
        assert db.execute('SELECT * FROM membership_reviews').fetchall()==original_review
    switch_user(client,key,target)
    assert client.get(f'/test-access/{institution}',headers=target_headers).status_code==403
    assert client.get(f'/test-mod/{institution}',headers=target_headers).status_code==403
    assert client.get('/api/v1/me',headers=target_headers).json()['displayName']=='Preserved name'
    assert client.get(f'/api/v1/membership-requests/{request_id}',headers=target_headers).json()['status']=='revoked'
    assert client.post(f'/api/v1/institutions/{institution}/membership-requests',json={},headers=target_headers).status_code==409
    switch_user(client,key,USER_ID)
    assert change(client,key,institution,target,'reinstate',1).status_code==200
    switch_user(client,key,target)
    assert client.get(f'/test-access/{institution}',headers=target_headers).status_code==200
    assert client.get(f'/test-mod/{institution}',headers=target_headers).status_code==403
    with psycopg.connect(url) as db:
        assert db.execute('SELECT count(*) FROM platform_administrators WHERE user_id=%s',(target,)).fetchone()[0]==0
        assert db.execute('SELECT * FROM membership_reviews').fetchall()==original_review
        assert db.execute('SELECT version,operation FROM membership_transitions ORDER BY version').fetchall()==[(1,'revoke'),(2,'reinstate')]


def test_moderator_can_change_students_but_target_moderator_requires_admin(identity_client):
    client,key,url=identity_client
    institution,target,_=active_seed(client,url,'moderator')
    assert change(client,key,institution,target,'revoke',0).status_code==200
    assert change(client,key,institution,target,'reinstate',1).status_code==200
    with psycopg.connect(url) as db:
        db.execute('INSERT INTO institution_moderators(institution_id,user_id) VALUES (%s,%s)',(institution,target))
    before=stored(url,target)
    denied=change(client,key,institution,target,'revoke',2)
    assert denied.status_code==403 and denied.json()['error']['code']=='PLATFORM_ADMINISTRATOR_REQUIRED'
    assert stored(url,target)==before
    with psycopg.connect(url) as db:db.execute('INSERT INTO platform_administrators(user_id) VALUES (%s)',(USER_ID,))
    assert change(client,key,institution,target,'revoke',2).status_code==200
    assert change(client,key,institution,target,'reinstate',3).status_code==200
    with psycopg.connect(url) as db:
        assert db.execute('SELECT user_id FROM institution_moderators WHERE institution_id=%s',(institution,)).fetchall()==[(USER_ID,)]


def test_revoked_target_stale_moderator_grant_is_not_reactivated(identity_client):
    client,key,url=identity_client
    institution,target,_=active_seed(client,url,'moderator')
    with psycopg.connect(url) as db:
        db.execute("UPDATE memberships SET status='revoked' WHERE user_id=%s",(target,))
        db.execute('INSERT INTO institution_moderators(institution_id,user_id) VALUES (%s,%s)',(institution,target))
    assert change(client,key,institution,target,'reinstate',0).status_code==403
    with psycopg.connect(url) as db:db.execute('INSERT INTO platform_administrators(user_id) VALUES (%s)',(USER_ID,))
    response=change(client,key,institution,target,'reinstate',0)
    assert response.status_code==200 and response.json()['transition']['moderatorGrantInvalidated'] is True
    with psycopg.connect(url) as db:
        assert db.execute('SELECT count(*) FROM institution_moderators WHERE user_id=%s',(target,)).fetchone()[0]==0


def test_platform_administrator_targets_and_self_actions_are_denied(identity_client):
    client,key,url=identity_client
    institution,target,_=active_seed(client,url)
    with psycopg.connect(url) as db:
        db.execute('INSERT INTO platform_administrators(user_id) VALUES (%s)',(target,))
        db.execute("INSERT INTO memberships(institution_id,user_id,request_id,status) VALUES (%s,%s,%s,'active')",(institution,USER_ID,uuid4()))
    for operation,state in [('revoke','active'),('reinstate','revoked')]:
        with psycopg.connect(url) as db:db.execute('UPDATE memberships SET status=%s',(state,))
        before=stored(url,target)
        response=change(client,key,institution,target,operation,0)
        assert response.status_code==403 and response.json()['error']['code']=='PLATFORM_ADMINISTRATOR_TARGET_FORBIDDEN'
        own=change(client,key,institution,USER_ID,operation,0)
        assert own.status_code==403 and own.json()['error']['code']=='SELF_MEMBERSHIP_ACTION_FORBIDDEN'
        assert stored(url,target)==before


def test_actor_student_blocked_removed_revoked_and_wrong_institution_denied(identity_client):
    client,key,url=identity_client
    institution,target,_=active_seed(client,url,'moderator')
    other=uuid4()
    with psycopg.connect(url) as db:
        db.execute('INSERT INTO institutions(id) VALUES (%s)',(other,))
        db.execute("INSERT INTO memberships(institution_id,user_id,request_id,status) VALUES (%s,%s,%s,'active')",(other,USER_ID,uuid4()))
    client.app.state.pilot_institution_id=other
    assert change(client,key,other,target,'revoke',0).status_code==403
    client.app.state.pilot_institution_id=institution
    for mutation in ["UPDATE profiles SET account_status='blocked' WHERE id=%s",
                     "UPDATE memberships SET status='revoked' WHERE user_id=%s",
                     'DELETE FROM institution_moderators WHERE user_id=%s']:
        with psycopg.connect(url) as db:db.execute(mutation,(USER_ID,))
        before=stored(url,target)
        for operation in ['revoke','reinstate']:
            response=change(client,key,institution,target,operation,0)
            assert response.status_code==403
        assert client.get(endpoint(institution,target),headers=auth(key)).status_code==403
        assert stored(url,target)==before
        with psycopg.connect(url) as db:
            db.execute("UPDATE profiles SET account_status='active' WHERE id=%s",(USER_ID,))
            db.execute("UPDATE memberships SET status='active' WHERE user_id=%s",(USER_ID,))
    # Ordinary active student with a spoofed token role has no administrative authority.
    assert client.post(endpoint(institution,target)+'/revoke',json={'reason':'Valid','expectedVersion':0},
                       headers=auth(key,role='admin',user_metadata={'is_admin':True})).status_code==403


def test_invalid_states_missing_targets_and_stale_versions_cannot_bypass_review(identity_client):
    client,key,url=identity_client
    institution,target,_=seed(client,url)
    for state in ['pending','rejected']:
        with psycopg.connect(url) as db:db.execute('UPDATE memberships SET status=%s WHERE user_id=%s',(state,target))
        before=stored(url,target)
        for operation in ['revoke','reinstate']:
            response=change(client,key,institution,target,operation,0)
            assert response.status_code==409 and response.json()['error']['code']=='MEMBERSHIP_TRANSITION_CONFLICT'
        assert stored(url,target)==before
    assert change(client,key,institution,uuid4(),'revoke',0).status_code==404
    with psycopg.connect(url) as db:
        profile_only=uuid4()
        db.execute('INSERT INTO profiles(id) VALUES (%s)',(profile_only,))
        db.execute("UPDATE memberships SET status='active' WHERE user_id=%s",(target,))
    assert change(client,key,institution,profile_only,'reinstate',0).status_code==404
    assert change(client,key,institution,target,'revoke',0).status_code==200
    response=change(client,key,institution,target,'revoke',0)
    assert response.status_code==409 and response.json()['error']['code']=='MEMBERSHIP_VERSION_CONFLICT'
    assert change(client,key,institution,target,'revoke',1).json()['error']['code']=='MEMBERSHIP_TRANSITION_CONFLICT'
    assert change(client,key,institution,target,'reinstate',1).status_code==200
    before=stored(url,target)
    assert change(client,key,institution,target,'revoke',0).status_code==409
    assert stored(url,target)==before


def test_validation_and_identity(identity_client):
    client,key,url=identity_client
    institution,target,_=active_seed(client,url)
    before=stored(url,target)
    bad=[{},None,[],{'reason':None,'expectedVersion':0},{'reason':' ','expectedVersion':0},
         {'reason':'x'*501,'expectedVersion':0},{'reason':4,'expectedVersion':0}]
    bad += [{'reason':'Valid','expectedVersion':v} for v in [-1,True,'0',0.5,None]]
    bad += [{'reason':'Valid','expectedVersion':0,f:'secret-input-marker'} for f in ['userId','actorId','status','roles','isAdmin','unknown']]
    for op in ['revoke','reinstate']:
        for payload in bad:
            response=client.post(endpoint(institution,target)+'/'+op,json=payload,headers=auth(key))
            assert response.status_code==422 and 'secret-input-marker' not in response.text
        for headers in [{},{'Authorization':'Bearer invalid'},auth(key,exp=1)]:
            assert client.post(endpoint(institution,target)+'/'+op,json={'reason':'Valid','expectedVersion':0},headers=headers).status_code==401
        assert client.post(endpoint(institution,target)+'/'+op+'?status=active',json={'reason':'Valid','expectedVersion':0},headers=auth(key)).status_code==422
    switch_user(client,key,USER_ID,email_confirmed_at=None)
    assert change(client,key,institution,target,'revoke',0).status_code==403
    assert stored(url,target)==before
    switch_user(client,key,USER_ID)
    assert client.get(endpoint(institution,target),headers=auth(key)).json()['version']==0
    assert change(client,key,institution,target,'revoke',0,' '+'x'*500+' ').json()['transition']['reason']=='x'*500
    assert change(client,key,institution,target,'reinstate',1,' x ').json()['transition']['reason']=='x'


def test_concurrent_transitions(identity_client):
    client,key,url=identity_client
    institution,target,_=active_seed(client,url)
    actors=[uuid4() for _ in range(6)]
    with psycopg.connect(url) as db:
        for actor in actors:
            db.execute('INSERT INTO profiles(id) VALUES (%s)',(actor,))
            db.execute('INSERT INTO platform_administrators(user_id) VALUES (%s)',(actor,))
    with ExitStack() as stack:
        clients=[]
        for actor in actors:
            config=client.app.state.identity_verifier.settings.model_copy(update={
                'database_url':client.app.state.identity_verifier.settings.database_url.__class__(url.replace('postgresql://','postgresql+psycopg://',1)),
                'pilot_institution_id':institution})
            other=stack.enter_context(TestClient(create_app(config)))
            other.app.state.identity_verifier.close()
            other.app.state.identity_verifier=verifier(key,user_data=user(id=str(actor)))
            clients.append(other)
        def round(version,mixed):
            def submit(i):
                return clients[i].post(endpoint(institution,target)+('/reinstate' if not mixed or i%2 else '/revoke'),
                    json={'reason':'Concurrent isolated test','expectedVersion':version},headers=auth(key,actors[i]))
            with ThreadPoolExecutor(max_workers=6) as pool:return list(pool.map(submit,range(6)))
        for responses in [round(0,True),round(1,False)]:
            assert sorted(r.status_code for r in responses)==[200,409,409,409,409,409]
    with psycopg.connect(url) as db:
        assert db.execute('SELECT status,transition_version FROM memberships WHERE user_id=%s',(target,)).fetchone()==('active',2)
        assert db.execute('SELECT version,operation FROM membership_transitions ORDER BY version').fetchall()==[(1,'revoke'),(2,'reinstate')]


def test_database_and_audit_failure_rollback(identity_client):
    client,key,url=identity_client
    institution,target,_=active_seed(client,url)
    with psycopg.connect(url) as db:db.execute('INSERT INTO institution_moderators(institution_id,user_id) VALUES (%s,%s)',(institution,target))
    before=stored(url,target)
    factory=client.app.state.session_factory
    class FailingSession(factory.class_):
        def commit(self):
            self.flush()
            self.execute(text('SELECT * FROM transition_secret_missing_table'))
    client.app.state.session_factory=lambda:FailingSession(bind=factory.kw['bind'])
    response=change(client,key,institution,target,'revoke',0)
    assert response.status_code==503 and 'transition_secret_missing_table' not in response.text and url not in response.text
    assert stored(url,target)==before
    client.app.state.session_factory=factory
    with psycopg.connect(url) as db:
        db.execute("CREATE FUNCTION test_transition_failure() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN RAISE EXCEPTION 'secret-audit-marker'; END; $$")
        db.execute('CREATE TRIGGER test_transition_failure BEFORE INSERT ON membership_transitions FOR EACH ROW EXECUTE FUNCTION test_transition_failure()')
    try:
        response=change(client,key,institution,target,'revoke',0)
        assert response.status_code==503 and 'secret-audit-marker' not in response.text
        assert stored(url,target)==before
    finally:
        with psycopg.connect(url) as db:
            db.execute('DROP TRIGGER test_transition_failure ON membership_transitions')
            db.execute('DROP FUNCTION test_transition_failure()')
    assert change(client,key,institution,target,'revoke',0).status_code==200
    with psycopg.connect(url) as db:
        for sql in ['DELETE FROM membership_transitions',"UPDATE membership_transitions SET reason='Changed'"]:
            with pytest.raises(psycopg.errors.RaiseException):db.execute(sql)
            db.rollback()


def test_authority_rechecked_before_writes_and_scoped_reads(identity_client):
    from app.db import get_session
    from app.modules.identity.router import get_current_profile
    from app.modules.identity.membership import pilot_institution
    client,key,url=identity_client
    institution,target,_=active_seed(client,url,'moderator')
    other=uuid4()
    with psycopg.connect(url) as db:db.execute('INSERT INTO institutions(id) VALUES (%s)',(other,))
    assert client.get(endpoint(other,target),headers=auth(key)).status_code==404
    assert change(client,key,other,target,'revoke',0).status_code==404
    def removed(institution_id=Depends(pilot_institution),profile=Depends(get_current_profile),session=Depends(get_session)):
        actor=require_membership_reviewer(institution_id,profile,session)
        with psycopg.connect(url) as db:db.execute('DELETE FROM institution_moderators WHERE user_id=%s',(USER_ID,))
        return actor
    client.app.dependency_overrides[require_membership_reviewer]=removed
    assert change(client,key,institution,target,'revoke',0).status_code==403
    assert stored(url,target)[0][0][1:]==('active',0) and stored(url,target)[2]==[]
    client.app.dependency_overrides.clear()


def test_transition_audit_constraints_rls_and_openapi(identity_client):
    client,key,url=identity_client
    institution,target,_=active_seed(client,url)
    assert change(client,key,institution,target,'revoke',0).status_code==200
    with psycopg.connect(url) as db:
        with pytest.raises(psycopg.errors.UniqueViolation):
            db.execute('INSERT INTO membership_transitions SELECT gen_random_uuid(),institution_id,target_user_id,request_id,actor_id,operation,from_status,to_status,version,reason,moderator_grant_invalidated,moderator_granted_at,occurred_at FROM membership_transitions')
        db.rollback()
        db.execute('CREATE ROLE transition_browser NOLOGIN')
        db.execute('GRANT USAGE ON SCHEMA public TO transition_browser')
        db.execute('GRANT SELECT,INSERT,UPDATE,DELETE ON membership_transitions TO transition_browser')
        db.execute('SET LOCAL ROLE transition_browser')
        assert db.execute('SELECT count(*) FROM membership_transitions').fetchone()[0]==0
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            db.execute("INSERT INTO membership_transitions(id,institution_id,target_user_id,request_id,actor_id,operation,from_status,to_status,version,reason,moderator_grant_invalidated) VALUES (%s,%s,%s,%s,%s,'reinstate','revoked','active',2,'Synthetic',false)",(uuid4(),institution,target,uuid4(),USER_ID))
        db.rollback()
    schema=client.get('/api/v1/openapi.json').json()
    contract=schema['components']['schemas']['TransitionInput']
    assert contract['additionalProperties'] is False and set(contract['required'])=={'reason','expectedVersion'}
    base='/api/v1/institutions/{institution_id}/memberships/{user_id}'
    for path,verb in [(base,'get'),(base+'/revoke','post'),(base+'/reinstate','post')]:
        route=schema['paths'][path][verb]
        assert route['security']==[{'HTTPBearer':[]}]
        assert route['responses']['422']['content']['application/json']['schema']['$ref'].endswith('/ErrorResponse')
