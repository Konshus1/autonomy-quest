import importlib.util,sys,json
spec=importlib.util.spec_from_file_location("solution",sys.argv[1]); m=importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
csvtext='id,observer,local_time,latitude,longitude,species,count,photo_hash,notes\na,A,2024-01-01T23:58:00-02:00,1.0,2.0,Fox,2,H,first\nb,B,2024-01-02T02:00:00+00:00,1.01,2.01,Vulpes,3,H,second\nc,C,2024-01-02T02:04:00+00:00,1.02,2.02,VULPES,1,,near\n'
def build(reverse=False):
 s=m.Survey(300,0.03); s.set_aliases({"fox":["Fox","Vulpes"]})
 if reverse:
  rows=list(__import__('csv').DictReader(csvtext.splitlines())); s.import_json(list(reversed(rows)))
 else:s.import_csv(csvtext)
 return s
s=build(); groups=s.possible_duplicates(); assert len(groups)==2
photo=next(g for g in groups if g['reason']=='photo'); near=next(g for g in groups if g['reason']=='possible')
assert photo['members']==['a','b'] and near['members'] in (['a','c'],['b','c'])
assert all(len(g['group_id'])==16 for g in groups)
raw=s.original_row('a'); assert raw['species']=='Fox' and raw['local_time'].endswith('-02:00') and raw['source']=='csv'
pre=s.summaries(); assert pre['daily']=={'2024-01-02':6}
s.decide(['a','b'],'same'); post=s.summaries(); assert post['daily']=={'2024-01-02':4} and post['species']=={'Fox':3,'VULPES':1}
s.decide(near['members'],'distinct'); assert near['members'] not in [g['members'] for g in s.possible_duplicates()]
s.import_json([{'id':'d','observer':'D','local_time':'2024-01-03T00:00:00+00:00','latitude':'9','longitude':'9','species':'Hare','count':'4','photo_hash':'','notes':'new'}])
rep=s.reconciliation_report(); assert any(d['decision']=='same' for d in rep['decisions']) and any(d['decision']=='distinct' for d in rep['decisions'])
# canonical serialization is independent of import order when source formats match
rows=list(__import__('csv').DictReader(csvtext.splitlines()))
x=m.Survey(300,.03); x.set_aliases({'fox':['Fox','Vulpes']}); x.import_json(rows)
y=m.Survey(300,.03); y.set_aliases({'fox':['Fox','Vulpes']}); y.import_json(list(reversed(rows)))
assert x.canonical_export()==y.canonical_export(); json.loads(x.canonical_export())
print('ok')
