import copy, importlib.util, sys
spec=importlib.util.spec_from_file_location("candidate",sys.argv[1]); m=importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
p={"greeting":"Hello <Pat>","free_text":"Your café balance is ready & waiting.",
 "address":["Pat Doe","10 Long Street"],
 "facts":[{"label":"Plan","value":"Gold","sensitivity":"public"},{"label":"Account","value":"12345678","sensitivity":"sensitive"}],
 "table":[["Item","Cost"],["Tea","£2"]],
 "action":[{"label":"Open & pay","url":"https://x.test/a?b=1&c=2","sms_alias":"go.x/a"},{"label":"Open again","url":"https://x.test/a?b=1&c=2","sms_alias":"go.x/a"}],
 "legal":"Legal wording must remain together.","signoff":"Thanks"}
saved=copy.deepcopy(p)
h=m.render_html(p)
assert "Hello &lt;Pat&gt;" in h and "ready &amp; waiting" in h
assert 'href="https://x.test/a?b=1&amp;c=2"' in h and h.count('<p class="action">')==2
assert "12345678" in h and "<table>" in h
text=m.render_text(p)
assert "Account: ****5678" in text and "Open & pay [1]" in text and "Open again [1]" in text
assert text.endswith("Links:\n[1] https://x.test/a?b=1&c=2")
pages=m.render_print(p,22,5)
assert pages==m.render_print(p,22,5) and all(len(x.splitlines())<=5 for x in pages)
joined="\n---PAGE---\n".join(pages)
assert "Account:" not in joined
for block in ("Pat Doe\n10 Long Street","Legal wording must\nremain together."):
 assert any(block in page for page in pages)
# An atomic block taller than a page is impossible.
too=copy.deepcopy(p); too["address"]=["a","b","c"]
try: m.render_print(too,20,2)
except ValueError: pass
else: raise AssertionError("oversize address accepted")
# Unicode code points count as one; trimming order preserves mandatory action/legal.
small={"greeting":"👋","free_text":"disposable words","address":[],"facts":[{"label":"X","value":"Y","sensitivity":"public"}],"table":[],"action":{"label":"Act","url":"https://long","sms_alias":"a.co"},"legal":"LEGAL","signoff":"Bye"}
required="Act a.co LEGAL"
assert m.render_sms(small,len(required))==required
assert len(m.render_sms(p,90))<=90 and "go.x/a" in m.render_sms(p,90) and "Legal wording" in m.render_sms(p,90)
try: m.render_sms(small,len(required)-1)
except ValueError: pass
else: raise AssertionError("impossible legal/action SMS accepted")
assert p==saved
print("ok")
