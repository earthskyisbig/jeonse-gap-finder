# -*- coding: utf-8 -*-
"""국토부 실거래(매매·전월세) 수집 → data.duckdb

    python src/collect.py --months 13

CI(GitHub Actions)에서 매달 맨바닥에서 돈다 — 영속 DB 가 없으므로
계산 창(12개월)+1 만 받는다. 키는 환경변수 PUBLIC_DATA_SERVICE_KEY.

원본 auction-pipeline 에서 실측으로 배운 규칙:
- 시군구 코드표를 믿지 않는다. 최근 1개월 프로브로 살아있는 코드만 확정
  (상위 시 코드·폐지 코드가 섞여 있다. 화성시는 구 분리로 4개 코드)
- 해제여부는 해제 건에만 'O', 나머지 NaN — `== 'O'` 로만 판정
- 코드성 컬럼은 문자열로 (숫자면 앞자리 0 이 날아간다)
- 마지막 페이지 당겨채움 중복 → nkey(내용 해시)로 제거
"""
import argparse
import datetime as dt
import hashlib
import os
import sys
import time
import warnings

warnings.filterwarnings('ignore')
import duckdb
import pandas as pd

KEY = os.getenv('PUBLIC_DATA_SERVICE_KEY')
if not KEY:
    try:
        from dotenv import load_dotenv
        load_dotenv()
        KEY = os.getenv('PUBLIC_DATA_SERVICE_KEY')
    except ImportError:
        pass
if not KEY:
    sys.exit('환경변수 PUBLIC_DATA_SERVICE_KEY 가 필요합니다')

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB = os.path.join(ROOT, 'data.duckdb')
SIDOS = ['서울특별시', '경기도']
EXTRA = {'경기도': ['41591', '41593', '41595', '41597']}   # 화성시 분리 구
# 위 코드는 코드표에 없어 이름이 빈다 — 실측 권역명
EXTRA_NAMES = {'41591': '화성시 남양·송산', '41593': '화성시 봉담·기안',
               '41595': '화성시 반월·기산', '41597': '화성시 동탄'}


def month_list(n):
    d = dt.date.today().replace(day=1)
    out = []
    for _ in range(n):
        out.append(f'{d.year}{d.month:02d}')
        d = (d - dt.timedelta(days=1)).replace(day=1)
    return out


def candidate_codes(sido):
    import PublicDataReader as pdr
    df = pdr.code_bdong()
    g = df[df['시도명'] == sido][['시군구코드', '시군구명']].drop_duplicates().dropna()
    g = g[g['시군구코드'].astype(str).str.len() == 5]
    rows = [(str(r.시군구코드), str(r.시군구명).strip()) for r in g.itertuples()]
    have = {c for c, _ in rows}
    rows += [(c, '') for c in EXTRA.get(sido, []) if c not in have]
    return sorted(set(rows))


def pick(df, *names):
    for n in names:
        if n in df.columns:
            return n
    return None


def clean(df, col):
    if col is None:
        return ''
    v = df[col].astype(str).str.strip()
    return v.mask(v.isin(['nan', 'None', 'NaT', '-']), '')


def normalize(df, kind, sido):
    c = {'apt': pick(df, '아파트', '단지명'), 'area': pick(df, '전용면적'),
         'y': pick(df, '년', '계약년도'), 'm': pick(df, '월', '계약월'),
         'dong': pick(df, '법정동'), 'jibun': pick(df, '지번'),
         'build': pick(df, '건축년도'), 'cancel': pick(df, '해제여부'),
         'dealing': pick(df, '거래유형')}
    o = pd.DataFrame(index=df.index)
    o['sigungu_code'] = df['sigungu_code'].astype(str).str.zfill(5)
    o['sido'] = sido
    o['dong'] = clean(df, c['dong'])
    o['jibun'] = clean(df, c['jibun'])
    o['apt_name'] = clean(df, c['apt'])
    o['area'] = pd.to_numeric(df[c['area']], errors='coerce')
    o['deal_ym'] = (df[c['y']].astype(str).str.zfill(4)
                    + df[c['m']].astype(str).str.zfill(2))
    o['build_year'] = pd.to_numeric(df[c['build']], errors='coerce')
    o['canceled'] = (df[c['cancel']].astype(str).str.strip() == 'O'
                     if c['cancel'] else False)
    o['dealing_gbn'] = clean(df, c['dealing'])
    if kind == 'sale':
        amt = pick(df, '거래금액')
        o['price_manwon'] = pd.to_numeric(
            df[amt].astype(str).str.replace(',', ''), errors='coerce')
    else:
        dep = pick(df, '보증금액', '보증금')
        mon = pick(df, '월세금액', '월세')
        o['deposit_manwon'] = pd.to_numeric(
            df[dep].astype(str).str.replace(',', ''), errors='coerce')
        o['monthly_manwon'] = pd.to_numeric(
            df[mon].astype(str).str.replace(',', ''), errors='coerce').fillna(0)
    o = o.dropna(subset=['area', 'price_manwon' if kind == 'sale'
                         else 'deposit_manwon'])
    parts = ['sigungu_code', 'dong', 'jibun', 'apt_name', 'area', 'deal_ym']
    val = 'price_manwon' if kind == 'sale' else 'deposit_manwon'
    o['nkey'] = [hashlib.sha1('|'.join(str(v) for v in row).encode())
                 .hexdigest()[:20]
                 for row in o[parts + [val]].itertuples(index=False)]
    return o.drop_duplicates('nkey')


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--months', type=int, default=13)
    a = ap.parse_args()

    import PublicDataReader as pdr
    api = pdr.TransactionPrice(KEY)
    yms = month_list(a.months)
    probe_ym = yms[1] if len(yms) > 1 else yms[0]

    live, names = [], {}
    for sido in SIDOS:
        cands = candidate_codes(sido)
        for code, nm in cands:
            try:
                d = api.get_data(property_type='아파트', trade_type='매매',
                                 sigungu_code=code, year_month=probe_ym,
                                 verbose=False)
                n = 0 if d is None else len(d)
            except Exception:
                n = 0
            if n > 0:
                live.append((code, sido))
                names[code] = nm or EXTRA_NAMES.get(code, code)
        print(f'{sido}: 살아있는 코드 {sum(1 for _, s in live if s == sido)}개',
              flush=True)

    con = duckdb.connect(DB)
    con.execute("create table if not exists sgg(code varchar, name varchar, "
                "sido varchar)")
    con.execute('delete from sgg')
    con.executemany('insert into sgg values (?,?,?)',
                    [(c, names[c], s) for c, s in live])

    for kind, ttype in (('sale', '매매'), ('rent', '전월세')):
        frames = []
        t0 = time.time()
        for i, (code, sido) in enumerate(live, 1):
            got = 0
            for ym in yms:
                try:
                    d = api.get_data(property_type='아파트', trade_type=ttype,
                                     sigungu_code=code, year_month=ym,
                                     verbose=False)
                except Exception as e:
                    print(f'  ! {code} {ym} 실패: {str(e)[:40]}', flush=True)
                    continue
                if d is None or not len(d):
                    continue
                d = d.copy()
                d['sigungu_code'] = code
                frames.append(normalize(d, kind, sido))
                got += len(d)
            if got == 0:
                print(f'  ! {code} {names.get(code)} {ttype} 0건 — 확인 필요',
                      flush=True)
            if i % 10 == 0:
                print(f'  [{i}/{len(live)}] {ttype} {int(time.time()-t0)}s',
                      flush=True)
        allf = pd.concat(frames, ignore_index=True).drop_duplicates('nkey')
        tbl = 'apt_trade' if kind == 'sale' else 'apt_rent'
        con.register('_t', allf)
        con.execute(f'create or replace table {tbl} as select * from _t')
        con.unregister('_t')
        print(f'{ttype}: {len(allf):,}행 적재', flush=True)

    # 수집 정합성 — 살아있는 코드 수와 적재된 코드 수가 같아야 한다
    for tbl in ('apt_trade', 'apt_rent'):
        n_code = con.execute(
            f'select count(distinct sigungu_code) from {tbl}').fetchone()[0]
        assert n_code >= len(live) * 0.95, f'{tbl} 시군구 누락: {n_code}/{len(live)}'
    con.close()
    print('수집 완료 →', DB)


if __name__ == '__main__':
    main()
