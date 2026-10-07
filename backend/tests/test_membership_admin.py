"""Real disposable PostgreSQL; actual JWT signatures and synthetic Supabase HTTP."""
from uuid import UUID,uuid4
import psycopg
import pytest
from tests.test_identity_auth import USER_ID, token

pytestmark=pytest.mark.postgres


def test_admin_approves_pending_request_atomically(identity_client):
    client,key,url=identity_client
    institution,target,request_id=uuid4(),uuid4(),uuid4()
    client.app.state.pilot_institution_id=institution
    with psycopg.connect(url) as db:
        db.execute('INSERT INTO profiles(id) VALUES (%s),(%s)',(USER_ID,target))
        db.execute('INSERT INTO institutions(id) VALUES (%s)',(institution,))
        db.execute('INSERT INTO platform_administrators(user_id) VALUES (%s)',(USER_ID,))
        db.execute('INSERT INTO memberships(institution_id,user_id,request_id) VALUES (%s,%s,%s)',(institution,target,request_id))
    response=client.post(f'/api/v1/institutions/{institution}/membership-requests/{request_id}/decision',
                         json={'decision':'approve','reason':'  Confirmed pilot student  '},
                         headers={'Authorization':f'Bearer {token(key)}'})
    assert response.status_code==200
    assert response.json()['status']=='active'
    with psycopg.connect(url) as db:
        assert db.execute('SELECT status FROM memberships WHERE user_id=%s',(target,)).fetchone()[0]=='active'
        assert db.execute('SELECT actor_id,target_user_id,decision,reason FROM membership_reviews').fetchall()==[(USER_ID,target,'approve','Confirmed pilot student')]
from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack
from fastapi import Depends
from fastapi.testclient import TestClient
from sqlalchemy import text
from app.main import create_app
from app.modules.identity.membership import require_active_membership
from app.modules.identity.membership_admin import require_membership_reviewer
from tests.test_identity_auth import user,verifier
from tests.test_identity_postgres import identity_client as base_identity_client

@pytest.fixture
def identity_client(base_identity_client):
    client,key,url=base_identity_client
    with psycopg.connect(url) as db:
        db.execute('TRUNCATE membership_reviews')
        db.execute('DELETE FROM institution_moderators')
        db.execute('DELETE FROM platform_administrators')
        db.execute('DELETE FROM memberships')
        db.execute('DELETE FROM institutions')
        db.execute('DELETE FROM profiles')
    yield client,key,url


def auth(key,subject=USER_ID,**claims):
    return {'Authorization':f'Bearer {token(key,sub=str(subject),**claims)}'}


def seed(client,url,reviewer_role='administrator',self_request=False):
    institution,target,request_id=uuid4(),USER_ID if self_request else uuid4(),uuid4()
    client.app.state.pilot_institution_id=institution
    with psycopg.connect(url) as db:
        for subject in set([USER_ID,target]):db.execute('INSERT INTO profiles(id) VALUES (%s)',(subject,))
        db.execute('INSERT INTO institutions(id) VALUES (%s)',(institution,))
        db.execute('INSERT INTO memberships(institution_id,user_id,request_id) VALUES (%s,%s,%s)',(institution,target,request_id))
        if reviewer_role=='administrator':db.execute('INSERT INTO platform_administrators(user_id) VALUES (%s)',(USER_ID,))
        if reviewer_role=='moderator':
            db.execute("INSERT INTO memberships(institution_id,user_id,request_id,status) VALUES (%s,%s,%s,'active')",(institution,USER_ID,uuid4()))
            db.execute('INSERT INTO institution_moderators(institution_id,user_id) VALUES (%s,%s)',(institution,USER_ID))
    return institution,target,request_id


def path(institution,request_id=None,decision=False):
    value=f'/api/v1/institutions/{institution}/membership-requests'
    if request_id:value+=f'/{request_id}'
    if decision:value+='/decision'
    return value


def current(url,target):
    with psycopg.connect(url) as db:
        return db.execute('SELECT request_id,status FROM memberships WHERE user_id=%s',(target,)).fetchone()


def audit(url):
    with psycopg.connect(url) as db:return db.execute('SELECT request_id,actor_id,target_user_id,institution_id,decision,reason,decided_at FROM membership_reviews ORDER BY id').fetchall()


def switch_user(client,key,subject,**changes):
    client.app.state.identity_verifier.close()
    client.app.state.identity_verifier=verifier(key,user_data=user(id=str(subject),**changes))


def protected_route(client):
    @client.app.get('/test-access/{institution_id}')
    def access(member=Depends(require_active_membership)):
        return {'ok':True}


def test_approval_grants_only_student_access_and_preserves_owner_status(identity_client):
    client,key,url=identity_client
    institution,target,request_id=seed(client,url)
    result=client.post(path(institution,request_id,True),json={'decision':'approve'},headers=auth(key))
    assert result.status_code==200 and result.json()['review']['actorId']==str(USER_ID)
    assert result.json()['review']['reason'] is None
    assert client.get(path(institution,request_id),headers=auth(key)).json()==result.json()
    with psycopg.connect(url) as db:
        assert db.execute('SELECT count(*) FROM platform_administrators WHERE user_id=%s',(target,)).fetchone()[0]==0
        assert db.execute('SELECT count(*) FROM institution_moderators WHERE user_id=%s',(target,)).fetchone()[0]==0
        assert db.execute('SELECT count(*) FROM memberships WHERE user_id=%s',(USER_ID,)).fetchone()[0]==0
    switch_user(client,key,target)
    owner=client.get(f'/api/v1/membership-requests/{request_id}',headers=auth(key,target))
    assert owner.status_code==200 and set(owner.json())=={'requestId','institutionId','status','requestedAt'}
    assert owner.json()['status']=='active'
    protected_route(client)
    assert client.get(f'/test-access/{institution}',headers=auth(key,target)).status_code==200


def test_rejection_resubmission_stale_id_cannot_change_new_request(identity_client):
    client,key,url=identity_client
    institution,target,request_id=seed(client,url)
    assert client.post(path(institution,request_id,True),json={'decision':'reject','reason':None},headers=auth(key)).status_code==200
    protected_route(client)
    switch_user(client,key,target)
    assert client.get(f'/test-access/{institution}',headers=auth(key,target)).status_code==403
    assert client.get(f'/api/v1/membership-requests/{request_id}',headers=auth(key,target)).json()['status']=='rejected'
    new=client.post(path(institution),json={},headers=auth(key,target))
    assert new.status_code==201 and new.json()['requestId']!=str(request_id)
    assert client.get(f'/api/v1/membership-requests/{request_id}',headers=auth(key,target)).status_code==404
    switch_user(client,key,USER_ID)
    before=current(url,target)
    stale=client.post(path(institution,request_id,True),json={'decision':'approve'},headers=auth(key))
    assert stale.status_code==409 and stale.json()['error']['code']=='MEMBERSHIP_REQUEST_NOT_PENDING'
    assert current(url,target)==before and len(audit(url))==1
    assert client.get(path(institution,request_id),headers=auth(key)).status_code==404
    assert client.post(path(institution,new.json()['requestId'],True),json={'decision':'approve'},headers=auth(key)).status_code==200
    assert len(audit(url))==2 and current(url,target)[1]=='active'


def test_moderator_scope_current_removal_revocation_and_blocking(identity_client):
    client,key,url=identity_client
    institution,target,request_id=seed(client,url,'moderator')
    assert client.get(path(institution),headers=auth(key)).status_code==200
    assert client.get(path(institution,request_id),headers=auth(key)).status_code==200
    other=uuid4()
    with psycopg.connect(url) as db:
        db.execute('INSERT INTO institutions(id) VALUES (%s)',(other,))
        db.execute("INSERT INTO memberships(institution_id,user_id,request_id,status) VALUES (%s,%s,%s,'active')",(other,USER_ID,uuid4()))
    client.app.state.pilot_institution_id=other
    assert client.get(path(other),headers=auth(key)).status_code==403
    assert client.get(path(other,request_id),headers=auth(key)).status_code==403
    assert client.post(path(other,request_id,True),json={'decision':'approve'},headers=auth(key)).status_code==403
    client.app.state.pilot_institution_id=institution
    for mutation in ["UPDATE profiles SET account_status='blocked' WHERE id=%s",
                     "UPDATE memberships SET status='revoked' WHERE user_id=%s",
                     'DELETE FROM institution_moderators WHERE user_id=%s']:
        with psycopg.connect(url) as db:db.execute(mutation,(USER_ID,))
        for method,endpoint in [('get',path(institution)),('get',path(institution,request_id)),('post',path(institution,request_id,True))]:
            result=getattr(client,method)(endpoint,headers=auth(key),**({'json':{'decision':'approve'}} if method=='post' else {}))
            assert result.status_code==403
        with psycopg.connect(url) as db:
            db.execute("UPDATE profiles SET account_status='active' WHERE id=%s",(USER_ID,))
            db.execute("UPDATE memberships SET status='active' WHERE user_id=%s",(USER_ID,))
    assert current(url,target)[1]=='pending' and audit(url)==[]


def test_student_removed_admin_and_self_approval_denied(identity_client):
    client,key,url=identity_client
    institution,target,request_id=seed(client,url,'student')
    headers=auth(key,role='admin',user_metadata={'role':'moderator'})
    for method,endpoint in [('get',path(institution)),('get',path(institution,request_id)),('post',path(institution,request_id,True))]:
        assert getattr(client,method)(endpoint,headers=headers,**({'json':{'decision':'approve'}} if method=='post' else {})).status_code==403
    with psycopg.connect(url) as db:
        db.execute('INSERT INTO platform_administrators(user_id) VALUES (%s)',(USER_ID,))
    assert client.get(path(institution),headers=auth(key)).status_code==200
    with psycopg.connect(url) as db:db.execute('DELETE FROM platform_administrators')
    assert client.post(path(institution,request_id,True),json={'decision':'approve'},headers=auth(key)).status_code==403
    with psycopg.connect(url) as db:
        db.execute('INSERT INTO platform_administrators(user_id) VALUES (%s)',(USER_ID,))
        own=uuid4()
        db.execute('INSERT INTO memberships(institution_id,user_id,request_id) VALUES (%s,%s,%s)',(institution,USER_ID,own))
    response=client.post(path(institution,own,True),json={'decision':'approve'},headers=auth(key))
    assert response.status_code==403 and response.json()['error']['code']=='SELF_APPROVAL_FORBIDDEN'
    assert current(url,USER_ID)[1]=='pending' and audit(url)==[]


def test_unknown_protected_reason_and_invalid_identity_rejected(identity_client):
    client,key,url=identity_client
    institution,target,request_id=seed(client,url)
    endpoint=path(institution,request_id,True)
    for payload in [{},{'decision':'revoked'},{'decision':'approve','reason':''},{'decision':'approve','reason':'  '},
                    {'decision':'reject','reason':'x'*501},{'decision':'approve','reason':4},None,[]]:
        assert client.post(endpoint,json=payload,headers=auth(key)).status_code==422
    for query in ['reviewerId=secret-input-marker','userId=secret-input-marker','status=active']:
        response=client.post(endpoint+'?'+query,json={'decision':'approve'},headers=auth(key))
        assert response.status_code==422 and 'secret-input-marker' not in response.text
        assert client.get(path(institution,request_id)+'?'+query,headers=auth(key)).status_code==422
    for field in ['userId','targetUserId','actorId','reviewerId','institutionId','status','roles','approvedAt','isAdmin','unknown']:
        response=client.post(endpoint,json={'decision':'approve',field:'secret-input-marker'},headers=auth(key))
        assert response.status_code==422 and 'secret-input-marker' not in response.text
    for headers in [{},{'Authorization':'Bearer invalid'},auth(key,exp=1)]:
        for method,p in [('get',path(institution)),('get',path(institution,request_id)),('post',endpoint)]:
            assert getattr(client,method)(p,headers=headers,**({'json':{'decision':'approve'}} if method=='post' else {})).status_code==401
    switch_user(client,key,USER_ID,email_confirmed_at=None)
    for method,p in [('get',path(institution)),('get',path(institution,request_id)),('post',endpoint)]:
        assert getattr(client,method)(p,headers=auth(key),**({'json':{'decision':'approve'}} if method=='post' else {})).status_code==403
    assert current(url,target)[1]=='pending' and audit(url)==[]


def test_concurrent_different_reviewers_make_one_decision(identity_client):
    client,key,url=identity_client
    institution,target,request_id=seed(client,url)
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
        def review(index):
            return clients[index].post(path(institution,request_id,True),headers=auth(key,actors[index]),
                                       json={'decision':'approve' if index%2 else 'reject'})
        with ThreadPoolExecutor(max_workers=6) as pool:responses=list(pool.map(review,range(6)))
    assert sorted(r.status_code for r in responses)==[200,409,409,409,409,409]
    winner=next(r.json() for r in responses if r.status_code==200)
    events=audit(url)
    assert len(events)==1 and events[0][1]==UUID(winner['review']['actorId'])
    assert events[0][4]==winner['review']['decision']
    assert current(url,target)[1]==('active' if events[0][4]=='approve' else 'rejected')


def test_listing_filters_pagination_detail_and_institution_scoping(identity_client):
    client,key,url=identity_client
    institution,target,request_id=seed(client,url)
    other=uuid4()
    with psycopg.connect(url) as db:
        db.execute('INSERT INTO institutions(id) VALUES (%s)',(other,))
        for scope,state in [(institution,'pending'),(institution,'pending'),(institution,'active'),(institution,'rejected'),(other,'pending')]:
            subject=uuid4()
            db.execute('INSERT INTO profiles(id) VALUES (%s)',(subject,))
            db.execute('INSERT INTO memberships(institution_id,user_id,request_id,status) VALUES (%s,%s,%s,%s)',(scope,subject,uuid4(),state))
    first=client.get(path(institution)+'?limit=2',headers=auth(key))
    assert first.status_code==200 and first.json()['hasMore'] is True and len(first.json()['items'])==2
    second=client.get(path(institution)+'?limit=2&offset=2',headers=auth(key)).json()
    assert len(second['items'])==1 and second['hasMore'] is False
    assert not {item['requestId'] for item in first.json()['items']} & {item['requestId'] for item in second['items']}
    for status,count in [('pending',3),('active',1),('rejected',1),('revoked',0),('all',5)]:
        response=client.get(path(institution)+f'?status={status}',headers=auth(key))
        assert response.status_code==200 and len(response.json()['items'])==count
        assert all(item['institutionId']==str(institution) for item in response.json()['items'])
    detail=client.get(path(institution,request_id),headers=auth(key))
    assert detail.status_code==200 and detail.json()['review'] is None
    for query in ['limit=0','limit=101','offset=-1','offset=10001','status=admin','institutionId='+str(other),'userId='+str(target),'role=admin']:
        assert client.get(path(institution)+'?'+query,headers=auth(key)).status_code==422
    assert client.get(path(other),headers=auth(key)).status_code==404
    assert client.get(path(institution,uuid4()),headers=auth(key)).status_code==404
    client.app.state.pilot_institution_id=other
    assert client.get(path(other,request_id),headers=auth(key)).status_code==404
    assert client.post(path(other,request_id,True),headers=auth(key),json={'decision':'approve'}).status_code==409
    assert audit(url)==[]


def test_commit_and_audit_failures_roll_back_whole_decision(identity_client):
    client,key,url=identity_client
    institution,target,request_id=seed(client,url)
    factory=client.app.state.session_factory
    class FailingSession(factory.class_):
        def commit(self):
            self.flush()
            self.execute(text('SELECT * FROM review_secret_missing_table'))
    client.app.state.session_factory=lambda:FailingSession(bind=factory.kw['bind'])
    response=client.post(path(institution,request_id,True),json={'decision':'approve'},headers=auth(key))
    assert response.status_code==503 and response.json()['error']['code']=='DATABASE_UNAVAILABLE'
    assert url not in response.text and 'review_secret_missing_table' not in response.text
    assert current(url,target)[1]=='pending' and audit(url)==[]
    client.app.state.session_factory=factory
    with psycopg.connect(url) as db:
        db.execute("CREATE FUNCTION test_audit_failure() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN RAISE EXCEPTION 'secret-audit-failure-marker'; END; $$")
        db.execute('CREATE TRIGGER test_audit_failure BEFORE INSERT ON membership_reviews FOR EACH ROW EXECUTE FUNCTION test_audit_failure()')
    try:
        response=client.post(path(institution,request_id,True),json={'decision':'reject'},headers=auth(key))
        assert response.status_code==503 and 'secret-audit-failure-marker' not in response.text
        assert current(url,target)[1]=='pending' and audit(url)==[]
    finally:
        with psycopg.connect(url) as db:
            db.execute('DROP TRIGGER test_audit_failure ON membership_reviews')
            db.execute('DROP FUNCTION test_audit_failure()')
    assert client.post(path(institution,request_id,True),json={'decision':'approve'},headers=auth(key)).status_code==200
    assert len(audit(url))==1


def test_reviewer_authority_is_rechecked_after_dependency(identity_client):
    from app.db import get_session
    from app.modules.identity.router import get_current_profile
    from app.modules.identity.membership import pilot_institution
    client,key,url=identity_client
    institution,target,request_id=seed(client,url,'moderator')
    def revoked_after_check(institution_id=Depends(pilot_institution),profile=Depends(get_current_profile),session=Depends(get_session)):
        reviewer=require_membership_reviewer(institution_id,profile,session)
        with psycopg.connect(url) as db:db.execute("UPDATE memberships SET status='revoked' WHERE user_id=%s",(USER_ID,))
        return reviewer
    client.app.dependency_overrides[require_membership_reviewer]=revoked_after_check
    response=client.post(path(institution,request_id,True),json={'decision':'approve'},headers=auth(key))
    assert response.status_code==403 and current(url,target)[1]=='pending' and audit(url)==[]
    client.app.dependency_overrides.clear()
    with psycopg.connect(url) as db:
        db.execute('INSERT INTO platform_administrators(user_id) VALUES (%s)',(USER_ID,))
        db.execute('DELETE FROM institution_moderators')
    def admin_removed_after_check(institution_id=Depends(pilot_institution),profile=Depends(get_current_profile),session=Depends(get_session)):
        reviewer=require_membership_reviewer(institution_id,profile,session)
        with psycopg.connect(url) as db:db.execute('DELETE FROM platform_administrators')
        return reviewer
    client.app.dependency_overrides[require_membership_reviewer]=admin_removed_after_check
    assert client.post(path(institution,request_id,True),json={'decision':'approve'},headers=auth(key)).status_code==403
    assert audit(url)==[] and current(url,target)[1]=='pending'


def test_valid_moderator_decision_reason_boundaries_and_repeat_conflict(identity_client):
    client,key,url=identity_client
    institution,target,request_id=seed(client,url,'moderator')
    response=client.post(path(institution,request_id,True),json={'decision':'approve','reason':' '+'x'*500+' '},headers=auth(key))
    assert response.status_code==200 and response.json()['review']['reason']=='x'*500
    for decision in ['approve','reject']:
        denied=client.post(path(institution,request_id,True),json={'decision':decision},headers=auth(key))
        assert denied.status_code==409 and denied.json()['error']['code']=='MEMBERSHIP_REQUEST_NOT_PENDING'
    assert len(audit(url))==1 and current(url,target)[1]=='active'


def test_review_audit_unique_immutable_rls_and_sanitized_read_failure(identity_client):
    client,key,url=identity_client
    institution,target,request_id=seed(client,url)
    assert client.post(path(institution,request_id,True),json={'decision':'reject'},headers=auth(key)).status_code==200
    with psycopg.connect(url) as db:
        with pytest.raises(psycopg.errors.UniqueViolation):
            db.execute("INSERT INTO membership_reviews(id,request_id,institution_id,actor_id,target_user_id,decision) VALUES (%s,%s,%s,%s,%s,'reject')",(uuid4(),request_id,institution,USER_ID,target))
        db.rollback()
        for mutation in ['DELETE FROM membership_reviews',"UPDATE membership_reviews SET decision='approve'"]:
            with pytest.raises(psycopg.errors.RaiseException):db.execute(mutation)
            db.rollback()
        db.execute('CREATE ROLE review_browser NOLOGIN')
        db.execute('GRANT USAGE ON SCHEMA public TO review_browser')
        db.execute('GRANT SELECT,INSERT,UPDATE,DELETE ON membership_reviews TO review_browser')
        db.execute('SET LOCAL ROLE review_browser')
        assert db.execute('SELECT count(*) FROM membership_reviews').fetchone()[0]==0
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            db.execute("INSERT INTO membership_reviews(id,request_id,institution_id,actor_id,target_user_id,decision) VALUES (%s,%s,%s,%s,%s,'reject')",(uuid4(),uuid4(),institution,USER_ID,target))
        db.rollback()
        db.execute('ALTER TABLE membership_reviews RENAME TO secret_audit_unavailable')
    try:
        response=client.get(path(institution,request_id),headers=auth(key))
        assert response.status_code==503 and response.json()['error']['code']=='DATABASE_UNAVAILABLE'
        assert 'secret_audit_unavailable' not in response.text and url not in response.text
    finally:
        with psycopg.connect(url) as db:db.execute('ALTER TABLE secret_audit_unavailable RENAME TO membership_reviews')


def test_review_openapi_security_and_safe_schema(identity_client):
    client,_,_=identity_client
    schema=client.get('/api/v1/openapi.json').json()
    collection='/api/v1/institutions/{institution_id}/membership-requests'
    detail=collection+'/{request_id}'
    for endpoint,verb in [(collection,'get'),(detail,'get'),(detail+'/decision','post')]:
        route=schema['paths'][endpoint][verb]
        assert route['security']==[{'HTTPBearer':[]}]
        assert route['responses']['422']['content']['application/json']['schema']['$ref'].endswith('/ErrorResponse')
    assert schema['components']['schemas']['RequestDecision']['additionalProperties'] is False
    assert set(schema['components']['schemas']['RequestDecision']['properties'])=={'decision','reason'}
    assert set(schema['components']['schemas']['OwnMembershipRequest']['properties'])=={'requestId','institutionId','status','requestedAt'}
