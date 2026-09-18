import pytest
pytest.importorskip("fastapi")
from fastapi.testclient import TestClient

def test_health_and_status(monkeypatch,tmp_path):
    import gpspoof
    with TestClient(gpspoof.app) as client:
        assert client.get('/api/health').status_code==200
        assert client.get('/api/status').json()['transport']=='PreferredRsdTunnel'
        assert client.post('/api/location/set',json={'latitude':91,'longitude':0}).status_code==422
        assert client.get('/api/diagnostics').json()['gpspoof_version']=='1.0.0'
