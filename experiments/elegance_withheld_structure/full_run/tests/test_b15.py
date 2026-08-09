import copy,hashlib,importlib.util,io,json,sys,zipfile
spec=importlib.util.spec_from_file_location('solution',sys.argv[1]); m=importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
def region(text):
 core={'text_claims':[text],'colors':['#000000'],'images':[]}; return dict(core,checksum=hashlib.sha256(json.dumps(core,sort_keys=True,separators=(',',':')).encode()).hexdigest())
c1={'id':'c1','based_on':None,'regions':{'front':region('A'),'back':region('B')}}
c2={'id':'c2','based_on':'c1','regions':{'front':copy.deepcopy(c1['regions']['front']),'back':region('B2'),'side':region('S')}}
prior={'c1':c1}; policy={'required_departments':['accessibility','brand','legal'],'required_regions':['back','front']}
r1={'id':'r1','candidate':'c1','department':'legal','decision':'approve','scope':['front'],'comment':'','supersedes':None}
r2={'id':'r2','candidate':'c1','department':'brand','decision':'approve','scope':['back'],'comment':'','supersedes':None}
r3={'id':'r3','candidate':'c2','department':'brand','decision':'approve','scope':['back','front'],'comment':'','supersedes':'r2'}
q=m.assess(c2,[r1,r2],policy,prior)
assert not q['ready'] and 'r2 stale: back' in q['stale'] and not any(x.startswith('r1 stale') for x in q['stale'])
assert 'accessibility missing approval' in q['blockers'] and 'legal approval lacks back' in q['blockers']
assert q['ancestry']==['back: changed in c2 (previous c1)','front: unchanged from c1 through c2','side: introduced in c2']
q2=m.assess(c2,[r1,r2,r3],policy,prior)
assert 'r2' in q2['superseded'] and 'r2 stale: back' in q2['stale'] and not any(x.startswith('brand') for x in q2['blockers'])
assert m.compare(c1,c2)=={'added':['side'],'removed':[],'changed':['back'],'unchanged':['front']}
# rejection is explained, and a change outside legal's scope does not stale r1
reject={'id':'r4','candidate':'c2','department':'accessibility','decision':'reject','scope':['back','front'],'comment':'contrast','supersedes':None}
qr=m.assess(c2,[r1,r2,r3,reject],policy,prior); assert 'accessibility rejected: contrast' in qr['blockers']
before=copy.deepcopy((c2,[r1,r2,r3],policy,prior)); z1=m.export_dossier(c2,[r1,r2,r3],policy,prior); z2=m.export_dossier(c2,[r1,r2,r3],policy,prior)
assert z1==z2 and (c2,[r1,r2,r3],policy,prior)==before and m.assess(c2,[r1,r2,r3],policy,prior)==q2
with zipfile.ZipFile(io.BytesIO(z1)) as z:
 assert z.namelist()==['candidate.json','reviews.json','readiness.json','checksums.json']
 assert all(i.date_time==(1980,1,1,0,0,0) and i.compress_type==zipfile.ZIP_STORED and i.create_system==0 and i.external_attr==(0o600 << 16) for i in z.infolist())
 checks=json.loads(z.read('checksums.json'))
 assert checks=={n:hashlib.sha256(z.read(n)).hexdigest() for n in ['candidate.json','reviews.json','readiness.json']}
print('ok')
