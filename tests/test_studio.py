import os
import sys
import tempfile
import io
from pathlib import Path
from datetime import datetime,timedelta

TEST_HOME = tempfile.mkdtemp(prefix='tazorixx-cloud-test-')
os.environ['HOME'] = TEST_HOME
os.environ['APP_ENV'] = 'test'
os.environ['STUDIO_SESSION_SECRET'] = 'test-session-secret-'+'Z'*40
os.environ['STUDIO_ADMIN_PASSWORD'] = 'super-long-private-test-password'
os.environ['GATEWAY_SESSION_ID'] = 'test-session'
os.environ['ENABLE_SENDING'] = 'false'
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'services'/'studio'))
from fastapi.testclient import TestClient
import api
from openpyxl import Workbook

client=TestClient(api.app)

def test_cloud_workflow():
    assert client.get('/api/studio/health').json()['ok']
    assert client.get('/api/studio/stats').status_code==401
    assert client.post('/api/studio/auth/login',json={'password':'wrong'}).status_code==401
    auth=client.post('/api/studio/auth/login',json={'password':os.environ['STUDIO_ADMIN_PASSWORD']})
    assert auth.status_code==200,auth.text
    csrf=auth.json()['csrf']
    assert client.get('/api/studio/auth/me').json()['csrf']==csrf
    assert client.post('/api/studio/sending/pause',json={'paused':True}).status_code==403
    headers={'X-CSRF-Token':csrf}
    wb=Workbook();sheet=wb.active;sheet.title='Business Details'
    sheet.append(['Business Name','Contact Number','Business Type','Selected Business Type','Website','WhatsApp','Address','Business Description','Services / Features'])
    sheet.append(['Cloud Test Interior','9876543210','Interior Designer','Interior Designers','Website not exist','9876543210','Pune, Maharashtra','Modern interiors with thoughtful plans','Design consultation, 3D planning'])
    out=io.BytesIO();wb.save(out)
    r=client.post('/api/studio/businesses/import',files={'file':('test.xlsx',out.getvalue(),'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')},headers=headers)
    assert r.status_code==200,r.text
    businesses=client.get('/api/studio/businesses').json()
    assert len(businesses)==1,businesses
    business=businesses[0]['id']
    profiles=client.get('/api/studio/industries').json()
    assert profiles
    first=profiles[0]
    change=client.post('/api/studio/industries/'+first['slug'],json={
        'category':'Updated test category',
        'headlines':['Updated test headline'],
        'introductions':['Updated test introduction']},headers=headers)
    assert change.status_code==200,change.text
    from PIL import Image
    photo=io.BytesIO();Image.new('RGB',(240,130),(60,150,210)).save(photo,'PNG')
    upload=client.post(f'/api/studio/businesses/{business}/images',data={'role':'hero','rights_note':'Original test image created by us','rights_confirmed':'yes'},files={'file':('demo.png',photo.getvalue(),'image/png')},headers=headers)
    assert upload.status_code==200,upload.text
    assert client.get(f'/api/studio/businesses/{business}').json()['images'][0]['media_name']=='demo.png'
    r=client.post(f'/api/studio/businesses/{business}/generate',json={},headers=headers)
    assert r.status_code==200,r.text
    assert client.get(f'/api/studio/businesses/{business}/preview').status_code==200
    removed=client.post(f'/api/studio/businesses/{business}/images/delete',json={'role':'hero','name':'demo.png'},headers=headers)
    assert removed.status_code==200,removed.text
    r=client.post(f'/api/studio/businesses/{business}/consent',json={'session_id':'test-session','recipient':'9876543210','consent_note':'Asked to see the website demo via phone'},headers=headers)
    assert r.status_code==200,r.text
    local=(datetime.now(tz=api.sender.KOLKATA)+timedelta(minutes=10)).strftime('%Y-%m-%d %H:%M')
    d,t=local.split()
    r=client.post('/api/studio/schedules',json={'business_id':business,'date':d,'time':t},headers=headers)
    assert r.status_code==200,r.text
    job_id=r.json()['job_id'];jobs=client.get('/api/studio/schedules').json()
    assert len(jobs)==1 and jobs[0]['status']=='scheduled'
    later=(datetime.now(tz=api.sender.KOLKATA)+timedelta(minutes=60)).strftime('%Y-%m-%d %H:%M')
    ld,lt=later.split()
    r=client.post(f'/api/studio/schedules/{job_id}/reschedule',json={'date':ld,'time':lt,'expected_utc':jobs[0]['schedule_utc']},headers=headers)
    assert r.status_code==200,r.text
    r=client.post(f'/api/studio/schedules/{job_id}/cancel',json={},headers=headers)
    assert r.status_code==200,r.text
    jobs=client.get('/api/studio/schedules').json();assert jobs[0]['status']=='cancelled'
    assert client.post(f'/api/studio/schedules/{job_id}/cancel',json={},headers=headers).status_code==409
    assert client.get('/api/studio/stats').json()['jobs']['cancelled']==1
    assert client.post('/api/studio/auth/logout',json={},headers=headers).status_code==200
    assert client.get('/api/studio/stats').status_code==401
    print('PASS: login, CSRF, Excel, profile edit, image upload/delete, website generation, preview, opt-in, IST schedule, reschedule, cancel, logout')
