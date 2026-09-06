"""Real loopback mTLS and V3 effects, NOT multi-host/hardware certification."""
from dataclasses import replace
from datetime import datetime,timedelta,timezone
import hashlib
import http.client
import ipaddress
import os
import ssl
import threading
import time
from cryptography import x509
from cryptography.hazmat.primitives import hashes,serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID,ExtendedKeyUsageOID
import pytest
from bossman_v3.fleet import FleetControlPlane,RemoteNodeTransport
from bossman_v3.fleet.node_agent import NodeExecutionRequest,NodeUnavailable,RemoteTransportUnavailable
from bossman_v3.fleet.remote_auth import (DispatchOutcomeUnknown,NodeAuthDenied,NodeAuthenticator,PeerCredential,canonical)
from bossman_v3.fleet.remote_rpc import NodeEndpoint,RemoteNodeGateway,RpcNodeClient,_identity,_request_dict,make_node_server
from tests.test_v3_fleet_e2e import World,_contract,_node,_node_bridge

def certificates(root):
    now=datetime.now(timezone.utc)
    ca_key=rsa.generate_private_key(public_exponent=65537,key_size=2048)
    ca_name=x509.Name([x509.NameAttribute(NameOID.COMMON_NAME,'ephemeral-fleet-ca')])
    ca=(x509.CertificateBuilder().subject_name(ca_name).issuer_name(ca_name)
        .public_key(ca_key.public_key()).serial_number(x509.random_serial_number())
        .not_valid_before(now-timedelta(minutes=1)).not_valid_after(now+timedelta(hours=1))
        .add_extension(x509.BasicConstraints(ca=True,path_length=0),True)
        .add_extension(x509.KeyUsage(False,False,False,False,False,True,True,False,False),True)
        .add_extension(x509.SubjectKeyIdentifier.from_public_key(ca_key.public_key()),False)
        .sign(ca_key,hashes.SHA256()))
    ca_path=root/'ca.pem';ca_path.write_bytes(ca.public_bytes(serialization.Encoding.PEM));pins={}
    for name,usage in [('node',ExtendedKeyUsageOID.SERVER_AUTH),('controller',ExtendedKeyUsageOID.CLIENT_AUTH)]:
        key=rsa.generate_private_key(public_exponent=65537,key_size=2048)
        cert=(x509.CertificateBuilder().subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME,name)]))
            .issuer_name(ca_name).public_key(key.public_key()).serial_number(x509.random_serial_number())
            .not_valid_before(now-timedelta(minutes=1)).not_valid_after(now+timedelta(hours=1))
            .add_extension(x509.BasicConstraints(ca=False,path_length=None),True)
            .add_extension(x509.KeyUsage(True,False,True,False,False,False,False,False,False),True)
            .add_extension(x509.ExtendedKeyUsage([usage]),False)
            .add_extension(x509.SubjectAlternativeName([x509.IPAddress(ipaddress.ip_address('127.0.0.1'))]),False)
            .add_extension(x509.AuthorityKeyIdentifier.from_issuer_public_key(ca_key.public_key()),False)
            .sign(ca_key,hashes.SHA256()))
        (root/f'{name}.pem').write_bytes(cert.public_bytes(serialization.Encoding.PEM))
        (root/f'{name}.key').write_bytes(key.private_bytes(serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,serialization.NoEncryption()))
        pins[name]=hashlib.sha256(cert.public_bytes(serialization.Encoding.DER)).hexdigest()
    client=ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT);client.minimum_version=ssl.TLSVersion.TLSv1_2;client.load_verify_locations(cafile=str(ca_path))
    client.load_cert_chain(root/'controller.pem',root/'controller.key')
    server=ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER);server.minimum_version=ssl.TLSVersion.TLSv1_2;server.verify_mode=ssl.CERT_REQUIRED
    server.load_verify_locations(cafile=str(ca_path));server.load_cert_chain(root/'node.pem',root/'node.key')
    return client,server,pins

@pytest.fixture
def stack(tmp_path):
    client_tls,server_tls,pins=certificates(tmp_path)
    plane=FleetControlPlane(tmp_path/'fleet.sqlite');plane.registry.register(_node('node-1'),now=time.time())
    world=World(tmp_path/'world');world.root.mkdir();local=plane.transport
    local.attach('node-1',_node_bridge(world,'node-1',tmp_path/'journals'))
    secret=os.urandom(32);expiry=time.time()+3600;scopes=frozenset({'probe','dispatch'})
    controller=NodeAuthenticator('controller',plane.store,
        (PeerCredential('node-1','rotation-1',secret,pins['node'],expiry,scopes),))
    node=NodeAuthenticator('node-1',plane.store,
        (PeerCredential('controller','rotation-1',secret,pins['controller'],expiry,scopes),))
    gateway=RemoteNodeGateway('node-1',node,local,controllers=frozenset({'controller'}))
    server=make_node_server(('127.0.0.1',0),server_tls,gateway)
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    endpoint=NodeEndpoint('127.0.0.1',server.server_address[1],'rotation-1',client_tls)
    client=RpcNodeClient(controller,{'node-1':endpoint},leases=plane.leases)
    contract=_contract(world,'work',['output.txt'],privacy='public');placement=plane.place(contract)
    assert placement.ok
    req=NodeExecutionRequest('work','m1','coder',placement.lease.lease_id,placement.lease.fence,contract,10)
    yield dict(locals())
    server.shutdown();server.server_close();thread.join(3)
    assert not thread.is_alive()

def test_real_tls_dispatch_verified_by_v3_no_duplicate(stack):
    s=stack;assert s['client'].probe('node-1')
    result=s['client'].dispatch('node-1',s['req'])
    assert s['world'].side_effects()==1
    assert (s['world'].root/'output.txt').read_text()=='1'
    assert s['contract'].validate(result)[0],result.reason
    assert not result.success
    assert 'fleet_dispatch' not in s['contract'].metadata
    with pytest.raises(DispatchOutcomeUnknown):s['client'].dispatch('node-1',s['req'])
    assert s['world'].side_effects()==1
    assert not s['client'].cancel('node-1','work')

def test_default_disabled_and_control_plane_binds_authority(stack):
    with pytest.raises(RemoteTransportUnavailable):RemoteNodeTransport().dispatch('node-1',None)
    s=stack;remote=RemoteNodeTransport(authenticator=s['controller'],endpoints={'node-1':s['endpoint']})
    plane=FleetControlPlane(s['plane'].store.path,transport=remote)
    assert remote.leases is plane.leases
    assert remote.probe('node-1')
    assert remote.dispatch('node-1',s['req']).executed
    assert not remote.cancel('node-1','work')
    assert not RemoteNodeTransport().probe('node-1')

def test_missing_client_cert_rejected(stack):
    s=stack;tls=ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT);tls.load_verify_locations(cafile=str(s['tmp_path']/'ca.pem'))
    conn=http.client.HTTPSConnection('127.0.0.1',s['endpoint'].port,context=tls,timeout=2)
    try:
        with pytest.raises((OSError,http.client.HTTPException)):
            conn.request('POST','/fleet/v1/probe',body=b'{}');conn.getresponse()
    finally:conn.close()
    assert s['world'].side_effects()==0

def test_wrong_pin_transmits_no_dispatch_body(stack):
    s=stack;old=next(iter(s['controller']._keys.values()))
    s['client'].auth=NodeAuthenticator('controller',s['plane'].store,(replace(old,certificate_sha256='0'*64),))
    assert not s['client'].probe('node-1')
    with pytest.raises(NodeAuthDenied):s['client'].dispatch('node-1',s['req'])
    assert s['world'].side_effects()==0

@pytest.mark.parametrize('privacy',['private','local_only','internal'])
def test_privacy_blocks_network(stack,privacy):
    s=stack;c=replace(s['contract'],privacy=privacy)
    with pytest.raises(NodeAuthDenied):s['client'].dispatch('node-1',replace(s['req'],contract=c))
    assert s['world'].side_effects()==0

@pytest.mark.parametrize('change',[{'fence':0},{'fence':True},{'timeout_seconds':float('nan')},
    {'timeout_seconds':-1},{'timeout_seconds':601},{'context_policy':'anything'},{'work_id':'other'},{'mission_id':'other'}])
def test_request_identity_schema_bounds(stack,change):
    s=stack
    with pytest.raises(NodeAuthDenied):s['client'].dispatch('node-1',replace(s['req'],**change))
    assert s['world'].side_effects()==0

def test_new_nonce_does_not_authorize_second_effect(stack):
    s=stack;s['client'].dispatch('node-1',s['req'])
    packet=s['controller'].issue('node-1','rotation-1','dispatch',_request_dict(s['req']))
    with pytest.raises(DispatchOutcomeUnknown):s['gateway'].handle(canonical(packet),'dispatch',s['pins']['controller'])
    assert s['world'].side_effects()==1

def test_changed_contract_cannot_spend_same_lease(stack):
    s=stack;body=_request_dict(s['req'])
    s['controller'].authorize_dispatch(_identity('node-1',s['req']),hashlib.sha256(canonical(body)).hexdigest())
    altered=replace(s['contract'],goal='different goal')
    packet=s['controller'].issue('node-1','rotation-1','dispatch',_request_dict(replace(s['req'],contract=altered)))
    with pytest.raises(DispatchOutcomeUnknown):s['gateway'].handle(canonical(packet),'dispatch',s['pins']['controller'])
    assert s['world'].side_effects()==0

def test_revoked_between_admission_and_effect(stack,monkeypatch):
    s=stack;original=s['local'].dispatch
    def revoke_then_execute(node,req,**kw):
        s['node'].revoke('controller','rotation-1');return original(node,req,**kw)
    monkeypatch.setattr(s['local'],'dispatch',revoke_then_execute)
    with pytest.raises(NodeUnavailable):s['client'].dispatch('node-1',s['req'])
    assert s['world'].side_effects()==0

def test_stale_lease_blocks_before_network(stack):
    s=stack;s['plane'].leases.release(s['placement'].lease)
    with pytest.raises(NodeAuthDenied):s['client'].dispatch('node-1',s['req'])
    assert s['world'].side_effects()==0

def test_authenticated_reply_not_execution_proof(stack):
    from bossman_v3.organization.models import WorkResult,Evidence
    s=stack
    class Liar:
        def execute(self,c,*,agent_id):
            return WorkResult(c.work_id,executed=True,success=True,produced_by=agent_id,
                evidence=[Evidence('file',str(s['world'].root/'output.txt'),True,source='untrusted-node')])
    s['local'].attach('node-1',Liar());result=s['client'].dispatch('node-1',s['req'])
    assert not result.success and not s['contract'].validate(result)[0]
    assert s['world'].side_effects()==0

def test_unsafe_configuration_rejected(stack):
    e=stack['endpoint']
    for changes in ({'host':'example.com'},{'host':'0.0.0.0'},{'port':True},{'privacy_levels':frozenset({'local_only'})}):
        with pytest.raises(ValueError):replace(e,**changes)
    tls=ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT);tls.check_hostname=False
    with pytest.raises(ValueError):replace(e,tls=tls)
    assert not stack['client'].probe('unknown')


def test_crash_after_external_effect_stays_ambiguous_no_retry(stack):
    s=stack
    class CrashAfterWrite:
        def execute(self,c,*,agent_id):
            path=s['world'].root/'effect.txt'
            path.write_text(path.read_text()+'x' if path.exists() else 'x')
            raise ConnectionError('power lost after effect')
    s['local'].attach('node-1',CrashAfterWrite())
    with pytest.raises(NodeUnavailable):s['client'].dispatch('node-1',s['req'])
    assert (s['world'].root/'effect.txt').read_text()=='x'
    with pytest.raises(DispatchOutcomeUnknown):s['client'].dispatch('node-1',s['req'])
    assert (s['world'].root/'effect.txt').read_text()=='x'


def test_remote_reply_cannot_cross_request_identity(stack,monkeypatch):
    s=stack;original=s['gateway'].handle
    def wrong_response(raw,operation,pin):
        from bossman_v3.fleet.remote_auth import decode_packet
        packet=decode_packet(original(raw,operation,pin))
        body=packet['payload'];body['request_nonce']='0'*32
        return canonical(s['node'].issue('controller','rotation-1',operation+'-response',body))
    monkeypatch.setattr(s['gateway'],'handle',wrong_response)
    assert not s['client'].probe('node-1')
    with pytest.raises(NodeAuthDenied):s['client'].dispatch('node-1',s['req'])
    # An untrusted reply cannot certify success, and a subsequent retry is blocked.
    assert s['world'].side_effects()==1
    with pytest.raises(DispatchOutcomeUnknown):s['client'].dispatch('node-1',s['req'])


@pytest.mark.parametrize('path,body,headers,expected',[
    ('/wrong',b'{}',{'Content-Type':'application/json'},400),
    ('/fleet/v1/probe',b'{}',{'Content-Type':'text/plain'},400),
    ('/fleet/v1/probe',b'{}',{'Content-Type':'application/json','Transfer-Encoding':'chunked'},400),
    ('/fleet/v1/probe',b'{}',{'Content-Type':'application/json','X-Node-Identity':'controller'},403),
])
def test_http_boundary_never_trusts_forwarded_identity(stack,path,body,headers,expected):
    s=stack;conn=http.client.HTTPSConnection('127.0.0.1',s['endpoint'].port,context=s['client_tls'],timeout=2)
    try:
        conn.request('POST',path,body=body,headers=headers)
        try:
            response=conn.getresponse();assert response.status==expected;response.read()
        except ConnectionResetError:
            # Invalid framing is rejected without draining an attacker-controlled
            # body. TLS may reset instead of delivering the 400 when unread
            # bytes remain. This is acceptable only for malformed requests;
            # credential rejections after a well-framed body must produce 403.
            assert expected == 400
    finally:conn.close()
    assert s['world'].side_effects()==0


def test_cloud_node_cannot_receive_unminimized_source(stack):
    from bossman_v3.fleet.models import CLOUD
    s=stack
    s['plane'].registry.register(_node('node-1',trust_class=CLOUD,privacy_level='public'),now=time.time())
    with pytest.raises(NodeAuthDenied):s['client'].dispatch('node-1',s['req'])
    # Even explicit MINIMIZED is refused BEFORE disclosure for a step-bearing contract.
    with pytest.raises(PermissionError):
        s['client'].dispatch('node-1',replace(s['req'],context_policy='MINIMIZED'))
    assert s['world'].side_effects()==0


def test_context_policy_revalidated_at_mutation_boundary(stack,monkeypatch):
    from bossman_v3.fleet.models import CLOUD
    s=stack;original=s['local'].dispatch
    def change_trust(node,request,**kw):
        s['plane'].registry.register(_node('node-1',trust_class=CLOUD,privacy_level='public'),now=time.time())
        return original(node,request,**kw)
    monkeypatch.setattr(s['local'],'dispatch',change_trust)
    with pytest.raises(NodeUnavailable):s['client'].dispatch('node-1',s['req'])
    assert s['world'].side_effects()==0
