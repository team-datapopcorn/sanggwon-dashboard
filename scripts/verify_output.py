import json, re, pathlib
d = json.load(open('docs/data/dashboard.json'))
js = pathlib.Path('docs/js/app.js').read_text()
html = pathlib.Path('docs/index.html').read_text()

print("== 키 존재 확인")
for k in ['meta','major','middle','small','sido','sgg','dong','floor','brand_cvs','brand_cafe',
          'brand_chicken','sgg_focus','seoul_gu','grid','history','pop_meta','sgg_density',
          'sido_density','national_per1k']:
    v = d.get(k)
    n = len(v) if isinstance(v, (list, dict)) else ('O' if v is not None else 'MISSING')
    print(f"  {k:16} {n}")

print("\n== 프론트가 읽는 키 대조")
for k in sorted(set(re.findall(r"D\.(\w+)", js))):
    print(f"  D.{k:16} {'OK' if k in d else 'MISSING <<<'}")

print("\n== CATS 대조")
cats = re.search(r"const CATS = \[([\s\S]*?)\];", js).group(1)
cats = [c.strip().strip("'\"") for c in cats.replace("\n", " ").split(",") if c.strip()]
g0 = d['grid'][0]
kf = set(d['sgg_focus'][0].keys())
print("  CATS:", cats)
print("  grid 누락:  ", [c for c in cats if c not in g0])
print("  focus 누락:", [c for c in cats if c not in kf])

print("\n== 지역비교 지표 옵션 대조")
sd = set(d['sgg_density'][0].keys())
sgg = set(d['sgg'][0].keys())
# cmpMetric select 블록만 뽑는다 (다른 select 는 value 가 숫자라 섞이면 안 된다)
block = re.search(r'<select id="cmpMetric">([\s\S]*?)</select>', html).group(1)
bad = []
for v, label in re.findall(r'<option value="(\w+)">([^<]*)</option>', block):
    if v in sd or v in sgg:
        continue
    bad.append((v, label))
print("  누락 지표:", bad if bad else "없음")

print("\n== 브랜드 합계 검증")
for k in ['brand_cvs', 'brand_cafe', 'brand_chicken']:
    tot = [r for r in d[k] if r['k'] == '__total__']
    s = sum(r['n'] for r in d[k] if r['k'] != '__total__')
    print(f"  {k:16} 합계행={tot[0]['n'] if tot else None} 실제합={s}")

print("\n== 값 범위 이상치 점검")
for name, key, field in [("시도 인구당", "sido_density", "per1k"), ("시군구 인구당", "sgg_density", "per1k")]:
    vals = [r[field] for r in d[key] if r.get(field)]
    print(f"  {name}: min={min(vals)} max={max(vals)} n={len(vals)}")
neg = [(r['sido'], r['sgg']) for r in d['grid'] if not (0 < r['lat'] < 39 and 124 < r['lon'] < 132)]
print("  grid 좌표 이상치:", neg if neg else "없음")

if bad or neg:
    raise SystemExit(1)
print("OK")
