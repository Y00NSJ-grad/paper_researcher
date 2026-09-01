# Paper Radar

개인 연구 관심사에 맞춰 여러 학술 소스에서 논문을 수집하고, 중복 제거와 점수화를
거쳐 Markdown 및 Slack으로 전달하는 연구 트렌드 파이프라인입니다.

Ubuntu에서는 다음 구조로 직접 실행합니다.

```text
systemd user timer → Python virtualenv → SQLite → Markdown/Slack
```

예약 작업이 없을 때 Paper Radar 프로세스는 실행되지 않습니다.

## 기능

- arXiv, OpenAlex, Semantic Scholar, OpenReview 최근 논문 검색
- Hugging Face Daily Papers 수집
- IEEE Xplore의 JSAC, TMC, TIV, ToN, TVT 최신 논문 검색
- DOI, arXiv ID, 제목·첫 저자·연도 기반 중복 제거
- 방법론 × 도메인 × 과업 태깅 및 설명 가능한 점수
- OpenAI Responses API 기반 선택적 여섯 필드 요약
- Daily Digest 및 7일/30일 Trend Map
- Slack Incoming Webhook 전달
- Ubuntu 사용자 systemd timer
- 수집 데이터·쿼리 실적·점수 책정 과정을 확인하는 읽기 전용 로컬 대시보드

## 요구 사항

- 인터넷에 접근할 수 있는 Ubuntu 서버
- `uv`가 관리하는 Python 3.12 가상환경
- 선택 사항: Slack Incoming Webhook
- 선택 사항: OpenAI API key

## Ubuntu 설치

코드가 `~/paper-radar`에 전송된 상태를 가정합니다.

### 1. 프로젝트로 이동

```bash
cd ~/paper-radar
```

### 2. uv와 Python 설치

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
export PATH="$HOME/.local/bin:$PATH"
uv python install 3.12
```

재접속 후에도 `uv`를 찾도록 설정합니다.

```bash
printf '\nexport PATH="$HOME/.local/bin:$PATH"\n' >> ~/.bashrc
source ~/.bashrc
```

### 3. 가상환경과 패키지 설치

OpenAI 패키지를 포함한 프로젝트 의존성을 설치합니다. API key가 비어 있으면
OpenAI 요청과 토큰 사용은 발생하지 않습니다.

```bash
uv sync --python 3.12 --extra dev
```

### 4. 테스트

```bash
uv run python -m unittest discover -s tests -v
uv run ruff check src tests
```

### 5. 환경 설정

```bash
cp .env.example .env
chmod 600 .env
nano .env
```

최소 권장 설정:

```dotenv
CONTACT_EMAIL=researcher@example.com
SLACK_WEBHOOK_URL=

OPENALEX_API_KEY=
SEMANTIC_SCHOLAR_API_KEY=
IEEE_XPLORE_API_KEY=
IEEE_XPLORE_ENABLED=false
OPENREVIEW_TOKEN=
HF_TOKEN=

OPENAI_API_KEY=
OPENAI_MODEL=

RADAR_USER_AGENT=paper-radar/0.1
RADAR_DB_PATH=data/papers.db
RADAR_OUTPUT_DIR=outputs
RADAR_CONFIG_PATH=config/keywords.yml
```

Slack을 사용하면 `SLACK_WEBHOOK_URL`을 입력합니다. OpenAI 요약을 사용하지 않으면
`OPENAI_API_KEY`와 `OPENAI_MODEL`을 비워둡니다.

Semantic Scholar, OpenReview, Hugging Face 공개 데이터는 토큰 없이도 조회할 수
있습니다. 더 높은 호출 한도나 비공개 데이터가 필요할 때 각 토큰을 설정합니다.
IEEE Xplore 수집기는 `IEEE_XPLORE_API_KEY`가 있고 `IEEE_XPLORE_ENABLED=true`일 때만
활성화됩니다. API 사용 승인을 기다리는 동안에는 기본값 `false`를 유지합니다.
IEEE 요청은 프로세스 간 최소 0.11초 간격으로 실행되며, UTC 기준 일일 호출량을
SQLite에 저장합니다. 하루 200회를 사용하면 다음 요청은 API 호출 전에 중단됩니다.

### 6. 환경변수 적용

프로젝트 디렉터리에서 `uv run paper-radar ...`를 실행하면 `.env`를 자동으로
읽습니다. 명령 실행 환경이나 systemd에 이미 지정된 변수는 `.env` 값보다 우선합니다.

### 7. SQLite 초기화

```bash
uv run paper-radar init-db
ls -lh data/papers.db
```

### 8. Slack 없는 최소 dry-run

```bash
uv run paper-radar daily \
  --dry-run \
  --limit-per-query 5 \
  --top 3 \
  --summarize 0
```

결과 확인:

```bash
find outputs -maxdepth 2 -type f -print
sed -n '1,200p' "outputs/daily/$(date +%F).md"
```

`--dry-run`은 SQLite와 Markdown을 생성하지만 Slack에는 전송하지 않습니다.

### 9. Slack 전송 시험

`.env`에 `SLACK_WEBHOOK_URL`을 입력한 뒤 실행합니다.

```bash
set -a
source .env
set +a

uv run paper-radar daily \
  --limit-per-query 5 \
  --top 3 \
  --summarize 0
```

### 10. 사용자 systemd 서비스 설치

서비스 파일은 프로젝트가 `~/paper-radar`에 있다고 가정합니다.

```bash
mkdir -p ~/.config/systemd/user
cp deploy/systemd/paper-radar@.service ~/.config/systemd/user/
cp deploy/systemd/paper-radar-daily.timer ~/.config/systemd/user/
cp deploy/systemd/paper-radar-weekly.timer ~/.config/systemd/user/
cp deploy/systemd/paper-radar-monthly.timer ~/.config/systemd/user/
```

### 11. timer 활성화

```bash
systemctl --user daemon-reload
systemctl --user enable --now paper-radar-daily.timer
systemctl --user enable --now paper-radar-weekly.timer
systemctl --user enable --now paper-radar-monthly.timer
```

### 12. SSH 로그아웃 후에도 실행되도록 설정

```bash
sudo loginctl enable-linger "$USER"
```

### 13. 예약 확인

```bash
systemctl --user list-timers 'paper-radar*'
```

### 14. 수동 실행과 로그 확인

```bash
systemctl --user start paper-radar@daily.service
journalctl --user -u paper-radar@daily.service -n 200 --no-pager
```

## 예약 시각

- Daily: 매일 06:30 KST, 최대 5분 무작위 지연
- Weekly: 일요일 09:00 KST, 최대 5분 무작위 지연
- Monthly: 매월 1일 09:30 KST, 최대 5분 무작위 지연

`Persistent=true`이므로 예약 시각에 서버가 꺼져 있었다면 다음 부팅 후 실행됩니다.

## 주요 명령

```bash
uv run paper-radar daily --since-hours 48 --top 10 --summarize 0
uv run paper-radar weekly
uv run paper-radar weekly --skip-collect --dry-run
uv run paper-radar monthly --days 30
```

OpenAI API key가 비어 있다면 `--summarize` 값과 관계없이 OpenAI 호출은 건너뜁니다.
명시적으로 요약을 끄려면 `--summarize 0`을 사용합니다.

Monthly 리포트는 최근 기간에 저장된 논문 제목·초록·태그를 GPT가 분석하여
Physical AI, Quantum AI, 도메인 동향을 각각 정리합니다. 분석 문장은 논문 링크를
근거로 제시하며, `OPENAI_API_KEY`가 없거나 호출에 실패하면 기존 정량 Trend Map으로
자동 폴백합니다.

## 로컬 대시보드

SQLite에 쌓인 데이터, 쿼리 실적, 점수 책정 과정, Trend Map을 브라우저에서 확인합니다.

```bash
uv run paper-radar dashboard          # http://127.0.0.1:8765
uv run paper-radar dashboard --port 9000
```

- 쓰기는 피드백 판정(`feedback` 테이블) 하나뿐입니다. 수집한 논문·점수·실행
  기록은 대시보드에서 변경되지 않고, 파이프라인을 실행하지도 않습니다.
- 기본적으로 loopback(`127.0.0.1`)에만 바인딩하고, `Host` 헤더가 loopback이
  아닌 요청은 거부합니다. 외부 공개용이 아닙니다.
- 표준 라이브러리 `http.server`만 사용하므로 추가 의존성이 없습니다.
- 원격 Ubuntu 서버에서 실행 중이라면 SSH 터널로 접근합니다.

  ```bash
  ssh -N -L 8765:127.0.0.1:8765 user@server
  ```

탭 구성:

| 탭 | 내용 |
| --- | --- |
| 개요 | 논문·버전·실행 건수, 수집 추이, 점수 분포, 소스별 수집량, 피드백 분류, `pipeline_runs` 기록 |
| 논문 | 검색·소스·태그·기간·점수·피드백 필터, 점수순/수집일자순/발행일자순 정렬, 코드 공개·서베이 배지, 논문별 점수 책정 내역과 매칭 용어, 소스 버전, 매칭된 쿼리, Keep/Maybe/Reject/Read 기록 |
| 쿼리 | 쿼리 추가·수정·삭제(`keywords.yml`에 저장), 쿼리별 논문 수·실행 수·평균 점수 |
| 태그 | 가중치와 보너스 규칙, 축별 태그·용어 추가·수정·삭제(`keywords.yml`에 저장) |
| 트렌드 맵 | 도메인 × 방법론/과업 히트맵, 태그 빈도, 반복 조합, 상위 도메인 추이 |
| 리포트 | `outputs/` 아래 생성된 Markdown 리포트를 daily / weekly / monthly로 나눠 열람 |

점수 책정 화면은 파이프라인과 동일한 `radar.scoring.explain_score`를 호출하므로,
`keywords.yml`을 수정한 뒤 아직 재수집하지 않은 논문은 저장된 점수와 현재 규칙의
점수 차이를 함께 표시합니다.

### 쿼리와 태그 편집

`쿼리` 탭에서 daily·weekly 검색식을, `태그` 탭에서 methods·domains·tasks의 태그와
용어를 추가·수정·삭제할 수 있습니다. 태그는 이름과 가중치, 그리고 매칭할 용어 목록을
가지며, 태그 옆에 그 태그가 붙은 DB 논문 수가 함께 표시됩니다.

저장은 **바뀐 블록만** 다시 씁니다. 쿼리를 고치면 `queries:`만, `domains`의 용어를
고치면 `domains:`만 교체되고 주석과 나머지 설정은 그대로 남습니다. 내용이 같으면
파일은 바이트 단위로 동일하게 유지되며, `terms: [a, b]`처럼 한 줄로 적힌 항목은
그 스타일이 보존됩니다.

안전장치:

- 쓰기 전에 결과를 다시 파싱해 편집한 값이 왕복하는지, 나머지 설정이 그대로인지
  확인합니다. 하나라도 어긋나면 파일은 손대지 않습니다.
- 임시 파일에 쓴 뒤 원자적으로 교체합니다.
- 대시보드를 연 뒤 에디터로 `keywords.yml`을 직접 고쳤다면 저장은 409로 거부됩니다.
  새로고침한 뒤 다시 저장하세요.
- 태그 이름은 영문·숫자·`_`·`-`만, 가중치는 0~100, 용어는 태그마다 최소 1개가
  필요합니다. 중복 태그와 중복 용어는 거부됩니다.

변경은 **다음 수집 실행부터** 적용됩니다. 이미 저장된 논문의 점수는 다시 계산되지
않으므로, 논문 상세의 점수 내역에 저장된 점수와 현재 규칙의 차이가 표시됩니다.

### 피드백 기록

논문 상세에서 Keep / Maybe / Reject / Read를 눌러 판정을 남깁니다.

- `feedback` 테이블에 **append**되며 이력이 남습니다. 가장 최근 행이 현재 판정이고,
  필터와 집계는 이 값을 따릅니다. `기록 지우기`는 해당 논문의 이력을 삭제합니다.
- 로컬 서버라도 다른 사이트가 브라우저를 통해 이 포트로 요청을 보낼 수 있으므로,
  쓰기 요청은 `application/json` 본문(CORS preflight를 유발하고 이 서버는 응답하지
  않음)과 loopback `Origin`을 함께 요구합니다. 폼 전송이나 교차 출처 요청은 403입니다.

## 설정 조정

`config/keywords.yml`에서 다음 항목을 조정합니다.

- `methods`, `domains`, `tasks`: 태그 및 가중치
- `queries.daily`: 매일 실행하는 직접 교차 검색
- `queries.weekly`: 넓은 탐색 검색
- `extra_anchors`: 전용 도메인이 없는 주제를 수집망에 추가
- `scoring.minimum_relevant`: DB와 Digest에 포함할 최소 점수

arXiv와 Hugging Face는 쿼리 문자열로 직접 검색하지 않습니다. 쿼리의 모든 단어를
AND로 묶으면 하루치 창에서 결과가 0건이 되기 때문에, 두 소스는 `domains` 전체 항목과
`extra_anchors`를 OR로 묶은 하나의 넓은 수집망을 기간으로 한정해 던진 뒤 `scoring`이
관련성을 판정합니다. 두 소스에서 쿼리 문자열은 수집된 논문을 어느 쿼리에 귀속시킬지
정하는 출처 라벨로만 쓰입니다. 수집 범위를 넓히거나 좁히려면 `domains`와
`extra_anchors`를 조정하세요. 나머지 소스는 쿼리 문자열을 그대로 검색어로 사용합니다.

첫 2주 동안은 검색식과 임계값을 조정하는 보정 기간으로 보는 것이 좋습니다.

## 아직 구현하지 않은 기능

- Slack Socket Mode 기반 Keep/Maybe/Reject 피드백
- preprint와 최종 출판본의 fuzzy 병합 검토 큐
- 서로 다른 연구실 수와 4주/12주 증가율 기반 Trend 승격
- SQLite 온라인 백업과 보존 정책
