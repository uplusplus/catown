import urllib.request
import json
import time

def get(path):
    r = urllib.request.urlopen('http://localhost:8000%s' % path, timeout=10)
    return r.status, json.loads(r.read())

def post(content, crid=1):
    data = json.dumps({'content': content}).encode()
    req = urllib.request.Request(
        'http://localhost:8000/api/chatrooms/%d/messages' % crid,
        data=data,
        headers={'Content-Type': 'application/json'},
        method='POST'
    )
    r = urllib.request.urlopen(req, timeout=30)
    return r.status, json.loads(r.read())

print('Before POST:')
st, _ = get('/api/status')
print('  status: %d' % st)

print('POST message...')
try:
    st, body = post('test rate limit check')
    print('  POST result: %d, id=%d' % (st, body.get('id')))
    time.sleep(10)
except Exception as e:
    print('  POST error: %s' % e)

print('After agent response:')
try:
    st, _ = get('/api/status')
    print('  status: %d' % st)
except Exception as e:
    print('  GET error: %s' % e)

try:
    st, body = post('second message')
    print('  second POST result: %d, id=%d' % (st, body.get('id')))
except Exception as e:
    print('  second POST error: %s' % e)
