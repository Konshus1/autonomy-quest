import copy,importlib.util,sys
from fractions import Fraction as F
spec=importlib.util.spec_from_file_location('solution',sys.argv[1]); m=importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
recipe={'title':'Cake','yield':F(3),'authored_text':'ORIGINAL EXACT\n','ingredients':[{'id':'i1','name':'milk','quantity':F(3,2),'unit':'cup','preparation':''},{'id':'i2','name':'egg','quantity':F(3),'unit':'each','preparation':'beaten'}],'equipment':['oven','whisk'],'instructions':['Whisk milk and egg','Bake until set'],'components':[]}
catalog=[{'id':'soy','for':'milk','priority':2,'result':{'name':'soy milk'}},{'id':'oat','for':'milk','priority':1,'result':{'name':'oat base','quantity_factor':F(2),'unit':'ml','preparation':'warm','wording_changes':[{'old':'Whisk milk','new':'Blend oat base'}],'remove_equipment':['whisk'],'add_equipment':['blender'],'additional_ingredients':[{'name':'casein','quantity':F(0),'unit':'g','preparation':''}]}},{'id':'pea','for':'casein','priority':1,'result':{'name':'pea protein','quantity_factor':F(1)}}]
before=copy.deepcopy((recipe,catalog)); request={'target_yield':F(5),'available_equipment':['oven','blender'],'excluded':['milk','casein']}
out=m.adapt(recipe,request,catalog)
assert out['possible'] and out['reasons']==[] and out['choices']==['oat','pea'] and out['warnings']==sorted(out['warnings'])
r=out['recipe']; assert r['yield']==F(5) and r['equipment']==['blender','oven']
main=next(i for i in r['ingredients'] if i['id']=='i1'); assert (main['name'],main['quantity'],main['unit'],main['preparation'])==('oat base',F(5),'ml','warm')
assert any(i['name']=='pea protein' for i in r['ingredients']) and not any(i['name'].lower() in {'milk','casein'} for i in r['ingredients'])
assert r['instructions']==['Blend oat base and egg','Bake until set']
assert (recipe,catalog)==before and m.print_original(recipe)=='ORIGINAL EXACT\n'
# Lower numeric priority makes the choice deterministic without constraining internal search/edit organization.
assert m.adapt(recipe,request,catalog)==out
bad=copy.deepcopy(recipe); bad['ingredients'].append({'id':'i3','name':'nuts','quantity':F(1),'unit':'cup','preparation':''})
badcat=[{'id':'cream','for':'milk','priority':1,'result':{'name':'cream','additional_ingredients':[{'name':'gelatin','quantity':F(1),'unit':'g','preparation':''}]}},{'id':'loop','for':'gelatin','priority':1,'result':{'name':'gelatin'}}]
f=m.adapt(bad,{'target_yield':F(5),'available_equipment':['oven','whisk'],'excluded':['milk','nuts','gelatin']},badcat)
assert not f['possible'] and f['recipe'] is None and f['reasons']==sorted(f['reasons'])
assert 'no substitution for nuts' in f['reasons'] and 'substitution cycle involving gelatin' in f['reasons'] and len(f['reasons'])>=2
assert bad['instructions']==['Whisk milk and egg','Bake until set']
print('ok')
