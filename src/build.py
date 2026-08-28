# -*- coding: utf-8 -*-
"""전세가율 갭 파인더 — data.duckdb 에서 계산해 docs/index.html 생성.

    python src/build.py

수집(src/collect.py) 직후 CI 에서 돈다. 계산 규칙(원본 auction-pipeline 실측):
- 매칭 키는 단지명이 아니라 법정동+지번+면적밴드 (이름 매칭은 오매칭을 낸다)
- 매매 중위값은 해제·직거래 제외, 창은 최근 12개월
- 전세 = 월세 0원 계약만 · 최근 6개월 전세 유무를 신선도 플래그로
- 취득세는 1주택 개인 약식 — 행마다 선계산, 여유자금만 입력으로
"""
import datetime as dt
import json
import os
import re

import duckdb
import pandas as pd

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB = os.path.join(ROOT, 'data.duckdb')
OUT = os.path.join(ROOT, 'docs', 'index.html')
MONTHS = 12
RECENT_RENT = 6

# 규제지역 — 2025-10-15 대책 기준: 서울 전역 + 경기 12곳
REGULATED_GG = {
    '과천시', '광명시', '성남시 분당구', '성남시 수정구', '성남시 중원구',
    '수원시 영통구', '수원시 장안구', '수원시 팔달구', '안양시 동안구',
    '용인시 수지구', '의왕시', '하남시',
}


def shift_ym(ym, back):
    y, m = int(ym[:4]), int(ym[4:])
    t = y * 12 + (m - 1) - back
    return f'{t // 12:04d}{t % 12 + 1:02d}'


def acq_tax_manwon(price_manwon, area_band):
    """1주택 개인 약식 취득세 (만원)."""
    eok = price_manwon / 10000
    if eok <= 6:
        rate = 0.01
    elif eok <= 9:
        rate = (eok * 2 / 3 - 3) / 100
    else:
        rate = 0.03
    total = rate + rate / 10
    if area_band > 85:
        total += 0.002
    return price_manwon * total


# 브랜드 판별 — 위에서부터 첫 매치. 이름에 시공사 브랜드가 없으면 '기타'.
BRANDS = [
    ('자이', r'자이'), ('래미안', r'래미안'), ('푸르지오', r'푸르지오'),
    ('힐스테이트', r'힐스테이트'), ('e편한세상', r'[eE이]편한세상'),
    ('아이파크', r'아이파크'), ('롯데캐슬', r'롯데캐슬|롯데캐슬'),
    ('더샵', r'더샵'), ('SK뷰', r'SK뷰|에스케이뷰'), ('포레나', r'포레나'),
    ('위브', r'위브'), ('어울림', r'어울림'), ('센트레빌', r'센트레빌'),
    ('하늘채', r'하늘채'), ('스위첸', r'스위첸'), ('데시앙', r'데시앙'),
    ('중흥S클래스', r'중흥S'), ('우미린', r'우미린'),
    ('호반', r'호반|베르디움'), ('더휴', r'더휴'), ('비발디', r'비발디'),
    ('아너스빌', r'아너스빌'), ('스타힐스', r'스타힐스'),
    ('주공·휴먼시아', r'주공|휴먼시아|엘에이치|LH'),
]


def brand_idx(name):
    s = str(name)
    for i, (_, pat) in enumerate(BRANDS):
        if re.search(pat, s):
            return i
    return len(BRANDS)                       # 기타


def main():
    con = duckdb.connect(DB, read_only=True)
    latest = con.execute('select max(deal_ym) from apt_trade').fetchone()[0]
    lo = shift_ym(latest, MONTHS - 1)
    rent_lo = shift_ym(latest, RECENT_RENT - 1)
    df = con.execute("""
    with s as (
      select sigungu_code, dong, jibun, cast(round(area) as int) as band,
             any_value(apt_name) as apt_name,
             any_value(build_year) as build_year,
             median(price_manwon) as sale_med, count(*) as n_sale
      from apt_trade
      where canceled=false and coalesce(dealing_gbn,'') <> '직거래'
        and deal_ym between ? and ? and jibun <> ''
      group by 1,2,3,4),
    r as (
      select sigungu_code, dong, jibun, cast(round(area) as int) as band,
             median(deposit_manwon) as rent_med, count(*) as n_rent,
             count(*) filter (deal_ym >= ?) as n_rent_recent
      from apt_rent
      where monthly_manwon = 0 and canceled=false
        and deal_ym between ? and ? and jibun <> ''
      group by 1,2,3,4)
    select s.*, r.rent_med, r.n_rent, r.n_rent_recent,
           s.sale_med - r.rent_med as gap
    from s join r using (sigungu_code, dong, jibun, band)
    """, [lo, latest, rent_lo, lo, latest]).df()
    names = dict(con.execute('select code, name from sgg').fetchall())
    sido_of = dict(con.execute('select code, sido from sgg').fetchall())
    con.close()

    df['name'] = df['sigungu_code'].map(names).fillna(df['sigungu_code'])
    df['sido'] = df['sigungu_code'].map(sido_of).map(
        {'서울특별시': '서울', '경기도': '경기'})
    df['reg'] = ((df['sido'] == '서울')
                 | df['name'].isin(REGULATED_GG)).astype(int)
    df['br'] = df['apt_name'].map(brand_idx)
    df['acq_tax'] = [acq_tax_manwon(p, b)
                     for p, b in zip(df['sale_med'], df['band'])]

    rows = [[r['name'], r.dong, str(r.apt_name), int(r.band),
             int(r.build_year) if pd.notna(r.build_year) else 0,
             int(r.sale_med), int(r.rent_med), int(r.gap),
             int(round(r.acq_tax)), int(r.n_sale), int(r.n_rent),
             int(r.n_rent_recent), int(r.reg), int(r.br), r.sido or '?']
            for _, r in df.iterrows()]
    sggs = {sd: sorted(df[df['sido'] == sd]['name'].unique().tolist())
            for sd in ('서울', '경기')}
    data_js = json.dumps(rows, ensure_ascii=False, separators=(',', ':'))
    brands_js = json.dumps([b for b, _ in BRANDS] + ['기타'],
                           ensure_ascii=False)
    sggs_js = json.dumps(sggs, ensure_ascii=False)

    n_total = len(df)
    stamp = (f'데이터 기준 {lo[:4]}.{lo[4:]}~{latest[:4]}.{latest[4:]} · '
             f'생성 {dt.date.today():%Y-%m-%d} · 매월 자동 갱신')
    doc = ('<!doctype html><html lang="ko"><head><meta charset="utf-8">\n'
           '<meta name="viewport" content="width=device-width,initial-scale=1">\n'
           '<title>전세가율 갭 파인더</title>\n'
           '<link rel="stylesheet" href="https://fonts.googleapis.com/css2?'
           'family=Noto+Serif+KR:wght@600;700&family=Noto+Sans+KR:wght@400;500;700'
           '&display=swap">\n<style>\n' + CSS + '\n</style></head><body>\n'
           + BODY.replace('__NTOTAL__', f'{n_total:,}')
                 .replace('__STAMP__', stamp)
           + f'<script>\nconst DATA={data_js};\nconst BRANDS={brands_js};\n'
             f'const SGGS={sggs_js};\n' + JS + '\n</script></body></html>\n')
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    open(OUT, 'w', encoding='utf-8').write(doc)
    kb = os.path.getsize(OUT) // 1024
    print(f'{n_total:,}개 조합 ({df.sido.value_counts().to_dict()}) '
          f'→ {OUT} ({kb:,}KB)')


CSS = """
:root{--bg:#FAF9F6;--panel:#FFF;--ink:#20242B;--mut:#69707C;--mut2:#98a0ab;
--line:#E4E1D8;--accent:#2E4B6B;--accent-soft:#EDF1F6;--ok:#1D7A46;--ok-soft:#E8F3EC;
--bad:#B3402F;--bad-soft:#F8ECE9;--mid:#8A6D1F;--mid-soft:#F7F1DE;--chip:#F1EFE8}
@media (prefers-color-scheme:dark){:root:not([data-theme="light"]){
--bg:#191B1F;--panel:#20242B;--ink:#E8E6E1;--mut:#9BA1AB;--mut2:#767d88;
--line:#33373F;--accent:#8FB0D4;--accent-soft:#26303C;--ok:#5DBB84;--ok-soft:#1F3228;
--bad:#E08A78;--bad-soft:#3A2723;--mid:#D4B95E;--mid-soft:#35301F;--chip:#2A2E36}}
:root[data-theme="dark"]{--bg:#191B1F;--panel:#20242B;--ink:#E8E6E1;--mut:#9BA1AB;
--mut2:#767d88;--line:#33373F;--accent:#8FB0D4;--accent-soft:#26303C;--ok:#5DBB84;
--ok-soft:#1F3228;--bad:#E08A78;--bad-soft:#3A2723;--mid:#D4B95E;--mid-soft:#35301F;
--chip:#2A2E36}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);
font:14px/1.55 'Noto Sans KR',sans-serif;font-variant-numeric:tabular-nums}
.wrap{max-width:1240px;margin:0 auto;padding:26px 18px 60px}
.eyebrow{font-size:11.5px;letter-spacing:.14em;color:var(--mut);
text-transform:uppercase;margin-bottom:5px}
h1{font-family:'Noto Serif KR',serif;font-size:25px;margin:0 0 6px;text-wrap:balance}
.lede{color:var(--mut);font-size:13px;max-width:88ch;margin-bottom:16px}
.layout{display:grid;grid-template-columns:288px 1fr;gap:18px;align-items:start}
@media(max-width:900px){.layout{grid-template-columns:1fr}}
.filters{background:var(--panel);border:1px solid var(--line);border-radius:14px;
padding:16px;position:sticky;top:12px;max-height:calc(100vh - 24px);overflow-y:auto}
.f{margin-bottom:14px}
.f label.t{display:block;font-size:11.5px;font-weight:700;color:var(--mut);
letter-spacing:.05em;margin-bottom:6px;text-transform:uppercase}
.seg{display:flex;gap:0;border:1px solid var(--line);border-radius:9px;overflow:hidden}
.seg button{flex:1;border:0;background:var(--panel);color:var(--ink);
padding:7px 0;font:inherit;font-size:13px;cursor:pointer}
.seg button.on{background:var(--accent);color:#fff;font-weight:700}
.pair{display:flex;gap:8px;align-items:center}
.pair input{width:100%}
input[type=number],input[type=text]{padding:7px 9px;border:1px solid var(--line);
border-radius:8px;background:var(--bg);color:var(--ink);font:inherit;
font-variant-numeric:tabular-nums}
input:focus{outline:2px solid var(--accent);outline-offset:0}
.sgbox{border:1px solid var(--line);border-radius:9px;max-height:170px;
overflow-y:auto;padding:6px 9px;background:var(--bg)}
.sgbox label{display:flex;gap:6px;align-items:center;font-size:12.5px;
padding:2px 0;cursor:pointer}
.sgbox .sd{font-size:11px;font-weight:700;color:var(--mut2);margin-top:4px}
.chips{display:flex;flex-wrap:wrap;gap:5px}
.chips button{border:1px solid var(--line);background:var(--panel);color:var(--ink);
border-radius:99px;padding:2px 10px;font:inherit;font-size:12px;cursor:pointer}
.chips button.on{background:var(--accent-soft);border-color:var(--accent);
color:var(--accent);font-weight:700}
.check{display:flex;gap:7px;align-items:center;font-size:13px;cursor:pointer;
margin:6px 0}
.btnreset{width:100%;background:var(--chip);color:var(--ink);
border:1px solid var(--line);border-radius:8px;padding:7px 0;font:inherit;
font-size:13px;cursor:pointer}
.btnreset:hover{border-color:var(--accent)}
.stats{display:grid;grid-template-columns:repeat(auto-fit,minmax(140px,1fr));
gap:10px;margin-bottom:14px}
.stat{background:var(--panel);border:1px solid var(--line);border-radius:12px;
padding:12px 15px}
.stat .k{font-size:11.5px;color:var(--mut)}
.stat .v{font-size:20px;font-weight:700}
section{background:var(--panel);border:1px solid var(--line);border-radius:14px;
margin:0 0 14px;overflow:hidden}
section>h2{font-family:'Noto Serif KR',serif;font-size:16px;margin:0;
padding:12px 18px 10px;border-bottom:1px solid var(--line)}
section>h2 small{font-family:'Noto Sans KR',sans-serif;font-weight:400;
color:var(--mut);font-size:11.5px;margin-left:8px}
.bd{padding:12px 18px}
.fbar{display:grid;grid-template-columns:130px 1fr 190px;gap:10px;
align-items:center;margin:5px 0;font-size:12.5px}
.fbar .track{background:var(--chip);border-radius:99px;height:8px;display:block}
.fbar .fill{background:var(--accent);border-radius:99px;height:8px;display:block}
.fbar .fv{color:var(--mut);font-size:11.5px;text-align:right}
table{border-collapse:collapse;width:100%;font-size:12.5px}
th{font-size:11px;color:var(--mut);text-align:left;padding:7px 8px;
border-bottom:1px solid var(--line);white-space:nowrap;cursor:pointer;
user-select:none}
th.n{text-align:right} th.sorted{color:var(--accent);font-weight:700}
td{padding:7px 8px;border-bottom:1px solid var(--line);vertical-align:top}
tr:last-child td{border-bottom:none} td.n{text-align:right}
.mut{color:var(--mut);font-size:12px} .mut2{color:var(--mut2);font-size:11px}
.badge{font-size:10.5px;font-weight:700;border-radius:6px;padding:1px 6px;
white-space:nowrap}
.badge.bad{background:var(--bad-soft);color:var(--bad)}
.badge.mid{background:var(--mid-soft);color:var(--mid)}
.rat.hi{color:var(--bad);font-weight:700} .rat.ok{color:var(--ok);font-weight:700}
.note{border-radius:10px;padding:10px 14px;font-size:12.5px;margin:10px 0 0;
background:var(--mid-soft);color:var(--mid)}
.scroller{overflow-x:auto}
footer{color:var(--mut2);font-size:11.5px;margin-top:16px;line-height:1.7}
"""

BODY = """
<div class="wrap">
<div class="eyebrow">서울·경기 아파트 · 실거래 12개월 중위값 · 지번 매칭</div>
<div class="mut" style="margin-bottom:4px">__STAMP__</div>
<h1>전세가율 갭 파인더</h1>
<div class="lede">매매·전세 실거래를 법정동+지번+면적밴드로 짝지은
__NTOTAL__개 단지·면적 조합. 필터를 조절하면 즉시 다시 계산된다.
매매 중위값은 해제·직거래 제외 · 필요자금 = 갭 + 취득세(1주택 약식) + 여유자금.</div>

<div class="layout">
<aside class="filters">
  <div class="f"><label class="t">지역</label>
    <div class="seg" id="segSido">
      <button data-v="" class="on">전체</button>
      <button data-v="서울">서울</button>
      <button data-v="경기">경기</button>
    </div>
    <input type="text" id="sgSearch" placeholder="시군구 검색"
      style="width:100%;margin:8px 0 6px">
    <div class="sgbox" id="sgList"></div>
  </div>
  <div class="f"><label class="t">예산 (필요자금 상한)</label>
    <div class="pair"><input type="number" id="budget" value="2" step="0.5" min="0">
      <span class="mut">억</span></div>
    <div class="pair" style="margin-top:6px">
      <input type="number" id="reserve" value="2000" step="500" min="0">
      <span class="mut">만원 여유</span></div>
  </div>
  <div class="f"><label class="t">전세가율 (%)</label>
    <div class="pair"><input type="number" id="rMin" value="80" min="0" max="200">
      <span class="mut">~</span>
      <input type="number" id="rMax" value="90" min="0" max="200"></div>
  </div>
  <div class="f"><label class="t">연식 · 준공</label>
    <div class="pair"><input type="number" id="ageMax" placeholder="연식 상한(년)">
      <span class="mut">년 이내</span></div>
  </div>
  <div class="f"><label class="t">전용면적 (㎡)</label>
    <div class="pair"><input type="number" id="aMin" placeholder="40">
      <span class="mut">~</span><input type="number" id="aMax" placeholder="135"></div>
  </div>
  <div class="f"><label class="t">브랜드</label>
    <div class="chips" id="brandChips"></div>
  </div>
  <div class="f"><label class="t">표본 · 안전장치</label>
    <div class="pair"><input type="number" id="minN" value="3" min="1">
      <span class="mut">건↑ (매·전 각각)</span></div>
    <label class="check"><input type="checkbox" id="fresh" checked>
      최근 6개월 전세 거래 있는 곳만</label>
    <label class="check"><input type="checkbox" id="noReg">
      규제지역 제외 (서울 전역 + 경기 12곳)</label>
  </div>
  <button class="btnreset" id="reset">필터 초기화</button>
</aside>

<main>
  <div class="stats">
    <div class="stat"><div class="k">후보</div><div class="v" id="stN">—</div></div>
    <div class="stat"><div class="k">전세가율 중위</div><div class="v" id="stR">—</div></div>
    <div class="stat"><div class="k">갭 중위</div><div class="v" id="stG">—</div></div>
    <div class="stat"><div class="k">필요자금 중위</div><div class="v" id="stC">—</div></div>
  </div>

  <section><h2>시군구 요약 <small>후보 수 상위 — 후보의 전세가율·갭 중위</small></h2>
    <div class="bd" id="sggBars"></div></section>

  <section><h2>후보 단지 <small id="tblCap"></small></h2>
    <div class="bd scroller"><table id="tbl">
      <thead><tr>
        <th data-k="loc">소재지 · 단지</th><th class="n" data-k="band">전용</th>
        <th class="n" data-k="year">준공</th><th class="n" data-k="sale">매매 중위</th>
        <th class="n" data-k="rent">전세 중위</th><th class="n sorted" data-k="ratio">전세가율 ▾</th>
        <th class="n" data-k="gap">갭</th><th class="n" data-k="need">필요자금</th>
        <th class="n" data-k="ns">매/전</th><th>플래그</th>
      </tr></thead><tbody id="tbody"></tbody></table></div>
    <div class="bd"><div class="note">전세가율 90% 이상은 무갭에 가까울수록
역전세·깡통 위험을 사는 것이다. 취득세는 1주택 개인 약식 — 다주택·법인은
8~13%대로 뛴다. 토지거래허가·전세대출 규제는 이 데이터에 없다.
후보라도 현장·규제 확인 전엔 결론이 아니다.</div></div></section>

  <footer>원천 trades.duckdb (국토부 실거래 · 서울 25구 + 경기 47시군구 · 36개월)
· 창 최근 12개월 중위값 · 전세 = 월세 0원 계약만 · 규제지역은 2025-10-15 대책 기준
· 코드·자동갱신: 이 저장소 (GitHub Actions 매월 1일) · "AI는 계산하고 후보를 좁힌다. 어디를 살지는 내가 정한다."</footer>
</main>
</div></div>
"""

JS = r"""
// DATA 행: [0시군구,1동,2단지,3면적,4준공,5매매,6전세,7갭,8취득세,
//           9매매n,10전세n,11최근전세n,12규제,13브랜드,14시도]
const $=id=>document.getElementById(id);
const eok=m=>Math.abs(m)>=10000?(m/10000).toFixed(2)+'억':Math.round(m).toLocaleString()+'만';
const st={sido:'',sgg:new Set(),brands:new Set(),sort:'ratio',desc:true};
const CUR=new Date().getFullYear();

function buildSgg(){
  const q=($('sgSearch').value||'').trim();
  const box=$('sgList');box.innerHTML='';
  for(const sd of ['서울','경기']){
    if(st.sido&&st.sido!==sd)continue;
    const names=SGGS[sd].filter(n=>!q||n.includes(q));
    if(!names.length)continue;
    const h=document.createElement('div');h.className='sd';h.textContent=sd;
    box.appendChild(h);
    for(const n of names){
      const l=document.createElement('label');
      const c=document.createElement('input');c.type='checkbox';
      c.checked=st.sgg.has(n);
      c.onchange=()=>{c.checked?st.sgg.add(n):st.sgg.delete(n);render();};
      l.appendChild(c);l.appendChild(document.createTextNode(n));
      box.appendChild(l);
    }
  }
}
function buildBrands(){
  const w=$('brandChips');w.innerHTML='';
  BRANDS.forEach((b,i)=>{
    const btn=document.createElement('button');
    btn.textContent=b;btn.className=st.brands.has(i)?'on':'';
    btn.onclick=()=>{st.brands.has(i)?st.brands.delete(i):st.brands.add(i);
      buildBrands();render();};
    w.appendChild(btn);
  });
}
function filtered(){
  const bud=(parseFloat($('budget').value)||Infinity)*10000;
  const res=parseFloat($('reserve').value)||0;
  const rMin=(parseFloat($('rMin').value)||0)/100,
        rMax=(parseFloat($('rMax').value)||999)/100;
  const ageMax=parseFloat($('ageMax').value)||null;
  const aMin=parseFloat($('aMin').value)||0,
        aMax=parseFloat($('aMax').value)||9999;
  const minN=parseInt($('minN').value)||1;
  const fresh=$('fresh').checked,noReg=$('noReg').checked;
  const out=[];
  for(const r of DATA){
    if(st.sido&&r[14]!==st.sido)continue;
    if(st.sgg.size&&!st.sgg.has(r[0]))continue;
    if(st.brands.size&&!st.brands.has(r[13]))continue;
    const ratio=r[6]/r[5];
    if(ratio<rMin||ratio>=rMax)continue;
    if(r[3]<aMin||r[3]>aMax)continue;
    if(ageMax!==null&&(!r[4]||CUR-r[4]>ageMax))continue;
    if(r[9]<minN||r[10]<minN)continue;
    if(fresh&&r[11]<1)continue;
    if(noReg&&r[12])continue;
    const need=r[7]+r[8]+res;
    if(need>bud)continue;
    out.push({r,ratio,need});
  }
  return out;
}
function median(a){if(!a.length)return null;
  const s=[...a].sort((x,y)=>x-y);const m=s.length>>1;
  return s.length%2?s[m]:(s[m-1]+s[m])/2;}
const KEYF={loc:x=>x.r[0]+x.r[1],band:x=>x.r[3],year:x=>x.r[4],
  sale:x=>x.r[5],rent:x=>x.r[6],ratio:x=>x.ratio,gap:x=>x.r[7],
  need:x=>x.need,ns:x=>x.r[9]};
function render(){
  const rows=filtered();
  $('stN').textContent=rows.length.toLocaleString()+'개';
  $('stR').textContent=rows.length?(median(rows.map(x=>x.ratio))*100).toFixed(1)+'%':'—';
  $('stG').textContent=rows.length?eok(median(rows.map(x=>x.r[7]))):'—';
  $('stC').textContent=rows.length?eok(median(rows.map(x=>x.need))):'—';

  const by={};
  for(const x of rows)(by[x.r[0]]=by[x.r[0]]||[]).push(x);
  const gs=Object.entries(by).map(([n,xs])=>({n,c:xs.length,
    r:median(xs.map(x=>x.ratio)),g:median(xs.map(x=>x.r[7]))}))
    .sort((a,b)=>b.c-a.c).slice(0,14);
  const mx=gs.length?gs[0].c:1;
  $('sggBars').innerHTML=gs.map(g=>
    `<div class="fbar"><span>${g.n}</span>`+
    `<span class="track"><span class="fill" style="width:${(g.c/mx*100).toFixed(0)}%"></span></span>`+
    `<span class="fv">${g.c}개 · ${(g.r*100).toFixed(0)}% · 갭 ${eok(g.g)}</span></div>`
  ).join('')||'<div class="mut">조건에 맞는 후보가 없다 — 필터를 넓혀 보라.</div>';

  const kf=KEYF[st.sort]||KEYF.ratio;
  rows.sort((a,b)=>st.desc?(kf(b)>kf(a)?1:-1):(kf(a)>kf(b)?1:-1));
  const top=rows.slice(0,100);
  $('tblCap').textContent=`전체 ${rows.length.toLocaleString()}개 중 상위 100 · 열 제목 클릭으로 정렬`;
  $('tbody').innerHTML=top.map(x=>{const r=x.r;
    const flags=[r[12]?'<span class="badge bad">규제</span>':'',
      r[3]<40?'<span class="badge mid">소형</span>':'',
      x.ratio>=.9?'<span class="badge bad">역전세위험</span>':''].join(' ');
    const rc=x.ratio>=.9?'hi':(x.ratio>=.8?'ok':'');
    return `<tr><td><b>${r[0]}</b> ${r[1]}<br><span class="mut2">${r[2]}</span></td>`+
      `<td class="n">${r[3]}㎡</td><td class="n">${r[4]||'—'}</td>`+
      `<td class="n">${eok(r[5])}</td><td class="n">${eok(r[6])}</td>`+
      `<td class="n rat ${rc}">${(x.ratio*100).toFixed(1)}%</td>`+
      `<td class="n">${eok(r[7])}</td><td class="n"><b>${eok(x.need)}</b></td>`+
      `<td class="n mut">${r[9]}/${r[10]}</td><td>${flags}</td></tr>`;
  }).join('');
}
document.querySelectorAll('#segSido button').forEach(b=>{
  b.onclick=()=>{document.querySelectorAll('#segSido button')
    .forEach(x=>x.classList.remove('on'));b.classList.add('on');
    st.sido=b.dataset.v;st.sgg.clear();buildSgg();render();};
});
$('sgSearch').oninput=buildSgg;
document.querySelectorAll('#tbl th[data-k]').forEach(th=>{
  th.onclick=()=>{const k=th.dataset.k;
    if(st.sort===k)st.desc=!st.desc;else{st.sort=k;st.desc=true;}
    document.querySelectorAll('#tbl th').forEach(x=>{x.classList.remove('sorted');
      x.textContent=x.textContent.replace(/ [▾▴]$/,'');});
    th.classList.add('sorted');th.textContent+=st.desc?' ▾':' ▴';
    render();};
});
['budget','reserve','rMin','rMax','ageMax','aMin','aMax','minN']
  .forEach(id=>$(id).oninput=render);
['fresh','noReg'].forEach(id=>$(id).onchange=render);
$('reset').onclick=()=>{st.sido='';st.sgg.clear();st.brands.clear();
  st.sort='ratio';st.desc=true;
  $('budget').value=2;$('reserve').value=2000;$('rMin').value=80;$('rMax').value=90;
  $('ageMax').value='';$('aMin').value='';$('aMax').value='';$('minN').value=3;
  $('fresh').checked=true;$('noReg').checked=false;
  document.querySelectorAll('#segSido button').forEach((x,i)=>
    x.classList.toggle('on',i===0));
  buildSgg();buildBrands();render();};
buildSgg();buildBrands();render();
"""


if __name__ == '__main__':
    main()
