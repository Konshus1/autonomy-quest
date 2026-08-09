from decimal import Decimal
from unittest.mock import patch
import json
import pytest
from runner.executor import (RemoteSubscriptionExecutor, Usage, AgentFailed, RateLimited,
                             _act_request_headers, DispatchUncertain)

class Response:
    def __init__(self, body): self.body=body; self.status=200
    def __enter__(self): return self
    def __exit__(self,*args): pass
    def read(self): return json.dumps(self.body).encode()

def signed(secret,payload):
    data=json.dumps(payload,sort_keys=True,separators=(",",":")).encode()
    return _act_request_headers(secret,data)

def test_remote_executor_preserves_result_and_zero_subscription_usage():
    body={"result":{"ok":True},"usage":{"tokens_in":0,"tokens_out":0,"cost_usd":"0"}}
    with patch("urllib.request.urlopen",return_value=Response(body)) as call:
        result,usage=RemoteSubscriptionExecutor("http://worker","token").run("p",{"type":"object"})
    assert result=={"ok":True} and usage.cost_usd==Decimal("0")
    headers=call.call_args.args[0].headers
    assert "X-aq-act-token" not in headers and "X-aq-act-signature" in headers
    assert "X-aq-act-nonce" in headers and "X-aq-act-timestamp" in headers

def test_remote_executor_fails_on_invalid_envelope():
    with patch("urllib.request.urlopen",return_value=Response({"result":[]})):
        with pytest.raises(AgentFailed,match="invalid envelope"):
            RemoteSubscriptionExecutor("http://worker","token").run("p",{})

def test_worker_endpoint_requires_rpc_token_and_returns_usage(monkeypatch, tmp_path):
    from fastapi.testclient import TestClient
    from runner import worker_api
    class Fake:
        calls=0
        def run(self,prompt,schema,tier,**kwargs):
            self.calls+=1
            return {"answer":"ok"},Usage(cost_usd=Decimal("0"))
    monkeypatch.setenv("AQ_ACT_RPC_TOKEN","rpc-secret")
    monkeypatch.setenv("AQ_ACT_RECEIPT_DIR",str(tmp_path))
    monkeypatch.setattr(worker_api,"_executor",Fake())
    client=TestClient(worker_api.app)
    payload={"prompt":"p","schema":{"type":"object"},"tier":"working","request_id":"test:normal:1"}
    assert client.post("/run",json=payload).status_code==403
    headers=signed("rpc-secret",payload)
    response=client.post("/run",json=payload,headers=headers)
    assert response.status_code==200 and response.json()["result"]=={"answer":"ok"}
    assert client.post("/run",json=payload,headers=headers).status_code==403
    cached=client.post("/run",json=payload,headers=signed("rpc-secret",payload))
    assert cached.status_code==200 and cached.json()==response.json()
    assert worker_api._executor.calls==1


def test_post_dispatch_rate_limit_is_uncertain_but_remote_preserves_true_429(monkeypatch,tmp_path):
    from fastapi.testclient import TestClient
    from runner import worker_api
    class Limited:
        def run(self,*args): raise RateLimited("plan exhausted",123)
    monkeypatch.setenv("AQ_ACT_RPC_TOKEN","rpc-secret")
    monkeypatch.setenv("AQ_ACT_RECEIPT_DIR",str(tmp_path))
    monkeypatch.setattr(worker_api,"_executor",Limited())
    payload={"prompt":"p","schema":{},"tier":"working","request_id":"test:limited:1"}
    response=TestClient(worker_api.app).post("/run",json=payload,headers=signed("rpc-secret",payload))
    assert response.status_code==409 and response.json()["detail"]["kind"]=="dispatch_uncertain"
    assert json.loads(next(tmp_path.glob("*.json")).read_text())["state"]=="rate-limited-uncertain"
    import urllib.error,io
    rate_body=json.dumps({"detail":{"kind":"rate_limited","message":"pre-dispatch limited","retry_after_s":123}}).encode()
    def limited(*args,**kwargs):
        raise urllib.error.HTTPError("http://worker/run",429,"limited",{},io.BytesIO(rate_body))
    with patch("urllib.request.urlopen",return_value=Response({"ok":True})):
        remote=RemoteSubscriptionExecutor("http://worker","token")
    with patch("urllib.request.urlopen",side_effect=limited):
        with pytest.raises(RateLimited) as raised:
            remote.run("p",{})
    assert raised.value.retry_after_s==123

def test_worker_health_fails_closed_until_subscription_auth(monkeypatch):
    from fastapi.testclient import TestClient
    from runner import worker_api
    monkeypatch.setattr(worker_api,"_executor",None)
    monkeypatch.setattr(worker_api,"SubscriptionExecutor",lambda *a,**k: (_ for _ in ()).throw(RuntimeError("not logged in")))
    response=TestClient(worker_api.app).get("/ready")
    assert response.status_code==503 and "not authenticated" in response.text


def test_remote_maps_durable_running_receipt_to_dispatch_uncertain():
    import urllib.error,io
    detail=json.dumps({"detail":{"kind":"dispatch_uncertain","message":"durably running"}}).encode()
    def uncertain(*args,**kwargs):
        raise urllib.error.HTTPError("http://worker/run",409,"conflict",{},io.BytesIO(detail))
    with patch("urllib.request.urlopen",return_value=Response({"ok":True})):
        remote=RemoteSubscriptionExecutor("http://worker","token")
    with patch("urllib.request.urlopen",side_effect=uncertain):
        with pytest.raises(DispatchUncertain,match="durably running"):
            remote.run("p",{},request_id="run:7:act")


def test_worker_exception_is_durable_dispatch_uncertain(monkeypatch,tmp_path):
    from fastapi.testclient import TestClient
    from runner import worker_api
    class Explodes:
        def run(self,*args,**kwargs): raise RuntimeError("after possible side effect")
    monkeypatch.setenv("AQ_ACT_RPC_TOKEN","rpc-secret")
    monkeypatch.setenv("AQ_ACT_RECEIPT_DIR",str(tmp_path))
    monkeypatch.setattr(worker_api,"_executor",Explodes())
    payload={"prompt":"p","schema":{},"tier":"working","request_id":"run:99:act"}
    response=TestClient(worker_api.app,raise_server_exceptions=False).post(
        "/run",json=payload,headers=signed("rpc-secret",payload))
    assert response.status_code==500
    assert response.json()["detail"]["kind"]=="dispatch_uncertain"
    receipt=json.loads(next(tmp_path.glob("*.json")).read_text())
    assert receipt["state"]=="failed-uncertain"
