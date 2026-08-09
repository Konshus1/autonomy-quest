import copy, importlib.util, sys
spec=importlib.util.spec_from_file_location("candidate",sys.argv[1]); m=importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
segments=[
 {"id":"s2","speaker":"Bob","start":10,"end":14,"text":"second item","tags":["work"],"private":[]},
 {"id":"s1","speaker":"Ada","start":0,"end":10,"text":"helloworld","tags":["intro"],"private":[]}]
saved=copy.deepcopy(segments); base=m.Workspace(segments)
attached=(base.add_note("n1","s1","source card")
              .add_link("web1","s1","https://archive.test/item/1")
              .mark_private("s1",5,10))
split=attached.split("s1",5,"s1a","s1b")
assert split.topic_index()["intro"]==["s1a","s1b"]
assert split.notes()==[{"id":"n1","text":"source card","segments":["s1a","s1b"]}]
assert split.links()==[{"id":"web1","url":"https://archive.test/item/1","segments":["s1a","s1b"]}]
assert split.export("researcher").splitlines()[0]=="0-5.0 Ada: hello"
assert split.export("public").splitlines()[1]=="5.0-10 Ada: [PRIVATE]"
joined=split.join("s1a","s1b","joined")
assert joined.notes()==[{"id":"n1","text":"source card","segments":["joined"]}]
assert joined.links()==[{"id":"web1","url":"https://archive.test/item/1","segments":["joined"]}]
assert joined.export("researcher").splitlines()[0]=="0-10 Ada: hello world"
assert joined.export("public").splitlines()[0]=="0-10 Ada: hello [PRIVATE]"
assert joined.speaker_index()=={"Ada":["joined"],"Bob":["s2"]}
assert joined.topic_index()=={"intro":["joined"],"work":["s2"]}
assert attached.compare(split)==["added s1a","added s1b","removed s1"]
# Independent changes combine, including text versus tags on one segment.
left=base.correct("s1","HELLOworld").correct("s2","SECOND item")
right=base.add_tag("s1","greeting")
merged=m.merge(base,left,right)
assert merged.export("researcher").splitlines()==["0-10 Ada: HELLOworld","10-14 Bob: SECOND item"]
assert merged.topic_index()["greeting"]==["s1"]
try: m.merge(base,base.correct("s1","version one"),base.correct("s1","version two"))
except m.ConflictError as e: assert str(e)=="conflict on s1: text"
else: raise AssertionError("same-text conflict was accepted")
# Stable ordering and all earlier values remain unchanged.
assert base.export("researcher").splitlines()==["0-10 Ada: helloworld","10-14 Bob: second item"]
assert base.notes()==[] and base.links()==[] and segments==saved
assert attached.notes()[0]["segments"]==["s1"]
assert split.notes()[0]["segments"]==["s1a","s1b"]
print("ok")
