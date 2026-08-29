# -*- coding: utf-8 -*-
"""로컬 파이프라인(auction-pipeline)의 계산 결과로 docs/index.html 생성.

    JEONSE_GAP_PARQUET=<jeonse_gap.parquet 경로> python src/build_from_parquet.py

수집을 GitHub Actions 에서 못 돌리므로(해외 IP 차단 — update.yml 참조)
로컬 스케줄러가 이 스크립트로 페이지를 만들고 docs 를 push 한다.
템플릿(BRANDS·CSS·BODY·JS)은 build.py 와 공유 — 화면이 갈라지지 않게.
"""
import datetime as dt
import json
import os
import sys

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from build import (BRANDS, BODY, CSS, JS, REGULATED_GG, ROOT, brand_idx,
                   shift_ym)  # noqa: E402

SRC = os.getenv('JEONSE_GAP_PARQUET')
if not SRC or not os.path.exists(SRC):
    sys.exit('JEONSE_GAP_PARQUET 환경변수로 parquet 경로를 지정하세요')
OUT = os.path.join(ROOT, 'docs', 'index.html')


def main():
    df = pd.read_parquet(SRC)
    df['name'] = df['sigungu'].astype(str)
    df['reg'] = ((df['sido'] == '서울')
                 | df['name'].isin(REGULATED_GG)).astype(int)
    df['br'] = df['apt_name'].map(brand_idx)

    rows = [[r['name'], r.dong, str(r.apt_name), int(r.band),
             int(r.build_year) if pd.notna(r.build_year) else 0,
             int(r.sale_med), int(r.rent_med), int(r.gap),
             int(round(r.acq_tax)), int(r.n_sale), int(r.n_rent),
             int(r.n_rent_recent), int(r.reg), int(r.br), r.sido or '?']
            for _, r in df.iterrows()]
    sggs = {sd: sorted(df[df['sido'] == sd]['name'].unique().tolist())
            for sd in ('서울', '경기')}
    data_js = json.dumps(rows, ensure_ascii=False, separators=(',', ':'))
    brands_js = json.dumps([b for b, _ in BRANDS] + ['기타'], ensure_ascii=False)
    sggs_js = json.dumps(sggs, ensure_ascii=False)

    today = dt.date.today()
    latest = f'{today.year}{today.month:02d}'
    lo = shift_ym(latest, 12)          # 표시용 근사 — 정확 창은 parquet 생성 시점
    stamp = f'생성 {today:%Y-%m-%d} · 최근 12개월 중위값 · 매월 자동 갱신'
    n_total = len(df)
    nl = chr(10)
    doc = ('<!doctype html><html lang="ko"><head><meta charset="utf-8">' + nl
           + '<meta name="viewport" content="width=device-width,initial-scale=1">' + nl
           + '<title>전세가율 갭 파인더</title>' + nl
           + '<link rel="stylesheet" href="https://fonts.googleapis.com/css2?'
             'family=Noto+Serif+KR:wght@600;700&family=Noto+Sans+KR:wght@400;500;700'
             '&display=swap">' + nl + '<style>' + nl + CSS + nl
           + '</style></head><body>' + nl
           + BODY.replace('__NTOTAL__', f'{n_total:,}').replace('__STAMP__', stamp)
           + '<script>' + nl + f'const DATA={data_js};' + nl
           + f'const BRANDS={brands_js};' + nl + f'const SGGS={sggs_js};' + nl
           + JS + nl + '</script></body></html>' + nl)
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    open(OUT, 'w', encoding='utf-8').write(doc)
    print(f'{n_total:,}개 조합 → {OUT} ({os.path.getsize(OUT)//1024:,}KB)')


if __name__ == '__main__':
    main()
