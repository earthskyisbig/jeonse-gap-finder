# 전세가율 갭 파인더

서울·경기 아파트 실거래(국토부 공공 API)로 전세가율·갭·필요자금을 계산해,
예산·지역·연식·면적·브랜드 필터로 갭투자 후보를 직접 조절하며 보는 정적 웹앱.

- **수집** `src/collect.py` — 매매+전월세 최근 13개월, 시군구 코드는 프로브로 확정
- **계산·생성** `src/build.py` — 법정동+지번+면적밴드 매칭, 12개월 중위값,
  해제·직거래 제외, 1주택 약식 취득세 → `docs/index.html` (데이터 내장, 서버 불요)
- **자동 갱신** `.github/workflows/update.yml` — 매월 1일 수집→생성→GitHub Pages 배포

## 로컬 실행

```bash
pip install -r requirements.txt
export PUBLIC_DATA_SERVICE_KEY=발급키        # data.go.kr 실거래가 API 활용신청
python src/collect.py --months 13
python src/build.py                          # → docs/index.html
```

## 배포 설정 (1회)

1. 저장소 Settings → Secrets → Actions 에 `PUBLIC_DATA_SERVICE_KEY` 등록
2. Settings → Pages → Source 를 **GitHub Actions** 로
3. Actions 탭에서 `update-and-deploy` 수동 실행 1회

## 읽는 법 · 한계

- 전세가율 = 전세 중위 ÷ 매매 중위. **높다 ≠ 좋다** — 90% 이상은 역전세·깡통
  위험이라 뱃지로 분리한다
- 취득세는 1주택 개인 약식(6억↓ 1.1%). 다주택·법인은 8~13%대
- 규제지역 표기는 2025-10-15 대책 기준. 토지거래허가·전세대출 규제는 API 에 없다
- 표본 3건 미만·최근 6개월 전세 공백은 기본 필터로 걸러진다 (조절 가능)

데이터: 국토교통부 실거래가 공개시스템 (아파트 매매/전월세, 공공누리).
계산은 후보를 좁힐 뿐, 최종 판단은 현장·규제 확인 후 사람이 한다.
