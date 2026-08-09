import copy, importlib.util, pathlib, sys
spec=importlib.util.spec_from_file_location("solution",sys.argv[1]); m=importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
ex={"title":"Sun & Moon","creators":[{"given":"Ada","family":"Ito"}],"date":{"start":"2020-01-02","end":"2021-03-04"},"dimensions_cm":[10.0,20.5],"paragraphs":[{"id":"p1","text":"Main 50%","optional":False},{"id":"p2","text":"Extra","optional":True}],"quotes":["Look"],"credits":["Gift & Fund"],"image_descriptions":["Circle × square"],"translations":{"fr":{"Sun & Moon":"Soleil & Lune","Main 50%":"Principal 50%","Look":"Voir","Gift & Fund":"Don & Fonds","Circle × square":"Cercle × carré"},"ja":{"Sun & Moon":"太陽 & 月","Main 50%":"主 50%","Extra":"追加","Look":"見る","Gift & Fund":"寄贈 & 基金","Circle × square":"円 × 四角"}}}
before=copy.deepcopy(ex)
r=m.generate_label(ex,"fr","wall")
assert r=={"text":"Soleil & Lune\nAda Ito\n02/01/2020–04/03/2021\n10 × 20.5 cm\nPrincipal 50%\nExtra\n«\u00a0Voir\u00a0»\nDon & Fonds","omissions":[],"fallbacks":["Extra"]}
mobile=m.generate_label(ex,"en","mobile",65)
assert mobile["omissions"]==["p2"] and "Gift & Fund" in mobile["text"] and "Extra" not in mobile["text"]
ja=m.generate_label(ex,"ja","wall")
assert "Ito Ada" in ja["text"] and "2020年1月2日–2021年3月4日" in ja["text"] and "「見る」" in ja["text"]
a=m.generate_label(ex,"en","audio")
assert "Sun and Moon" in a["text"] and "Main 50 percent" in a["text"] and "Circle times square" in a["text"] and a["omissions"]==[]
assert ex==before and m.generate_label(ex,"fr","wall")==r
try: m.generate_label(ex,"de","wall"); raise AssertionError("bad locale accepted")
except ValueError: pass
import locale
old=locale.setlocale
locale.setlocale=lambda *x: (_ for _ in ()).throw(AssertionError("global locale used"))
try: m.generate_label(ex,"fr","wall")
finally: locale.setlocale=old
print("ok")
