# slash-pro-mcp

[English](https://github.com/sigcrew/slash-pro-mcp/blob/main/README.md)

[Slash Pro](https://app.slash.pro)용 [MCP](https://modelcontextprotocol.io) 서버입니다. Claude Code·Codex·Cursor·VS Code Copilot 같은 AI 코딩 에이전트가 워크스페이스의 티켓·회의·PRD·위키를 읽고 쓸 수 있게 해 줍니다.

로컬 **stdio** 클라이언트이고, 도구 하나가 Slash Pro REST 호출 하나(또는 몇 개)를 감쌉니다. 인증은 **본인의 개인 API 토큰**으로 합니다. 권한은 서버가 판단하고, 이 패키지는 권한을 따로 더하지 않습니다. 이 패키지가 더하는 몇 가지 제한은 안전망일 뿐 보안 경계가 아닙니다([보안](#보안) 참고).

## 준비

- [uv](https://docs.astral.sh/uv/)(`uvx` 포함) 또는 Python 3.12+와 `pipx`
- Slash Pro 워크스페이스와 개인 API 토큰(`et_…`) — 웹 앱 설정에서 발급
- 워크스페이스 id — 웹 앱 주소에 있는 UUID

## 빠른 시작

```bash
# 토큰을 화면에 보이지 않게 입력받아 셸 기록에 남기지 않고 본인만 읽을 수 있는 파일로 저장
(umask 077; mkdir -p ~/.config/slash-pro; read -rs T; printf '%s' "$T" > ~/.config/slash-pro/token)

claude mcp add slash-pro -s user \
  -e SLASH_PRO_WORKSPACE_ID=<워크스페이스-uuid> \
  -- uvx slash-pro-mcp==0.1.0
```

macOS·Linux에서는 토큰 파일을 그룹·다른 사용자가 읽을 수 있으면 서버가 시작을 거부합니다. Windows에는 이 권한 검사가 없으니 사용자 프로필 안에 두세요.

설치 확인: `uvx slash-pro-mcp==0.1.0 --help`. 예시는 버전을 고정합니다(`slash-pro-mcp==0.1.0`). 새 버전은 [변경 기록](https://github.com/sigcrew/slash-pro-mcp/blob/main/CHANGELOG.md)을 확인한 뒤 직접 올리세요.

Codex는 서버 시작을 기본 10초만 기다리므로 첫 실행(패키지 내려받기)을 위해 `~/.codex/config.toml`에 `startup_timeout_sec = 30`을 넣으세요:

```toml
[mcp_servers.slash-pro]
command = "uvx"
args = ["slash-pro-mcp==0.1.0"]
startup_timeout_sec = 30

[mcp_servers.slash-pro.env]
SLASH_PRO_WORKSPACE_ID = "<워크스페이스-uuid>"
```

## 설정

| 변수 | 기본값 | 뜻 |
|---|---|---|
| `SLASH_PRO_WORKSPACE_ID` | (필수) | 워크스페이스 UUID |
| `SLASH_PRO_TOKEN` | — | 개인 API 토큰. 없으면 파일에서 읽음 |
| `SLASH_PRO_TOKEN_FILE` | `~/.config/slash-pro/token` | 토큰 파일 (`chmod 600` 필수) |
| `SLASH_PRO_API_URL` | `https://echo-api.slash.pro` | API 주소. 커스텀 호스트를 허용하지 않았다면 `https://echo-api.slash.pro`(경로는 자유)만 허용. 호스트는 소문자 ASCII |
| `SLASH_PRO_ALLOW_CUSTOM_API` | — | **개발·자체 호스팅 전용.** 허용할 호스트를 정확히 적음(예: `localhost`, `api.example.com`; `1`은 거부). 루프백(localhost·127.0.0.1·[::1])도 이 설정이 필요하고, `http://`는 루프백에서만 허용. 이때 토큰은 `SLASH_PRO_TOKEN` 값으로만 받고, 토큰 파일(기본·이전·`SLASH_PRO_TOKEN_FILE`)은 절대 읽지 않음 |
| `SLASH_PRO_WORKSPACE_RULES` | `1` | `0`이면 규칙 본문을 instructions에 넣지 않음(에이전트 편집 차단은 그대로) |
| `SLASH_PRO_AGENT` | — | `list_handoffs`에 `to`가 없을 때 볼 받는 이 라벨. 없으면 모든 받는 이의 인계를 보여 줌. 맡기·노트는 항상 토큰 라벨로 기록 |
| `SLASH_PRO_AGENT_MODEL` | — | 모델 별칭. `X-Agent-Model`로 보내 활동 기록에 남김 |
| `SLASH_PRO_REQUESTED_BY` | — | 요청자 표시를 명시적으로 덮어쓸 때만 사용. 기본은 서버가 토큰 주인으로 정함 |

> **토큰을 프로젝트 설정 파일(`.cursor/mcp.json`·`.vscode/mcp.json`·`.mcp.json`)에 직접 적지 마세요.** 이런 파일은 커밋됩니다. 토큰 파일, `${env:…}`·`${input:…}` 참조, 사용자 단위 설정을 쓰세요.

**이전 이름(사용 중단 예정)**: `ECHO_API`·`ECHO_WS`·`ECHO_TOKEN`·`ECHO_TOKEN_FILE`·`ECHO_AGENT`·`ECHO_AGENT_MODEL`·`ECHO_REQUESTED_BY`와 `~/.claude/echo-token`·`~/.claude/echo-env` 파일도 계속 읽습니다(env 파일은 워크스페이스 id나 토큰이 아직 없을 때만 읽고, 그룹·다른 사용자가 읽을 수 있으면 경고만 하되 `ECHO_TOKEN`이 들어 있으면 시작을 거부하고, 다른 사용자가 쓸 수 있거나 본인 소유가 아니면 거부합니다). `SLASH_PRO_*` 값이 없을 때만 쓰이고, 쓰이면 stderr에 한 줄 안내가 나옵니다. 다음 메이저 버전에서 제거합니다.

## 클라이언트별 설정

Codex·Cursor·VS Code·Claude Desktop·Gemini CLI·Windsurf 설정 예시는 [README.md › Client setup](https://github.com/sigcrew/slash-pro-mcp/blob/main/README.md#client-setup)에 있습니다(모두 `uvx slash-pro-mcp==0.1.0`). VS Code는 최상위 키가 `servers`이고, `inputs`에 `"password": true`를 주면 토큰을 파일에 적지 않고 입력받아 안전하게 저장합니다.

## 도구

전체 도구 표는 [README.md › Tools](https://github.com/sigcrew/slash-pro-mcp/blob/main/README.md#tools)에 있습니다. 도구 설명은 영어입니다. 묶음별로 보면:

| 묶음 | 도구 |
|---|---|
| 티켓 | `list_tickets`(프로젝트 또는 워크스페이스 전체, 상태·담당·막힘·PRD 필터(상태마다 서버 필터 호출 후 합침), 만든 시각 최신순, 기본 50·최대 200개 + "N개 더" 줄) · `get_ticket` · `create_ticket` · `update_ticket_status`(backlog·todo·in_progress·review·done — canceled는 사유가 필요해 웹에서. done을 누가 넘길지는 워크스페이스 규칙) · `update_ticket` · `add_note` · `update_note`(이 세션의 일반 노트만, 서버는 작성 10분 안에만 허용) · `delete_note`(이 세션에서 이 에이전트가 쓴 노트만) · `relate_ticket` · `delete_relation` · `replace_checklist`(에이전트 항목만 — 다른 에이전트가 쓴 판정 없는 항목도 바뀜, 사람 항목·리뷰 판정 유지, 판정 있는 항목은 빼거나 문구를 바꿀 수 없음) · `list_evidence` · `add_evidence` · `delete_evidence`(이 세션에서 넣은 근거만) · `mark_deployed` |
| 검증·인계·준비도 | `start_verification` · `add_verification` · `add_handoff` · `list_handoffs` · `claim_handoff` · `heartbeat_handoff` · `complete_handoff` · `block_handoff` · `ask_questions` · `list_questions` · `readiness_refresh` |
| 아웃컴 | `list_outcomes` · `get_outcome` · `create_outcome` · `update_outcome` · `add_outcome_evidence` · `add_ticket_to_outcome` · `remove_ticket_from_outcome` |
| 회의 | `list_meetings` · `list_upcoming_meetings` · `get_meeting` · `get_meeting_summary` · `get_minutes` · `find_transcript_excerpts` · `add_todo` · `add_agenda_item` |
| 위키·PRD·검색 | `wiki_tree` · `get_wiki_page` · `search_wiki` · `write_wiki_page` · `create_wiki_page` · `list_doc_updates` · `accept_doc_update` · `revert_doc_update` · `accept_all_doc_updates` · `reject_doc_update` · `refresh_doc_update` · `list_prds` · `get_prd` · `create_prd` · `assign_ticket_prd` · `search` |
| 프로젝트 | `create_project` · `update_project` |

티켓 부모 변경·병합·아웃컴 상태 전환·중단 사유·판정·관련 티켓·문서 연결·아웃컴 승격·준비도 질문 답변·티켓 취소·프로젝트 보관/삭제·토큰 관리는 **사람만** 할 수 있는 일이라서 도구로 만들지 않았습니다. AI 문서 업데이트의 수락·되돌리기·거절은 그 업데이트의 문서(page_id)를 확인하고, 워크스페이스 규칙 페이지에는 하지 않습니다.

## 보안

- **권한 경계는 토큰입니다.** 필요한 스코프만 골라 발급하세요. 읽기만 할 때는 `tickets:read`·`wiki:read`·`meetings:read`면 되고, 쓰기까지 하는 일반 에이전트라면 `tickets:write`·`wiki:write`를 더합니다. `projects:write`를 주면 워크스페이스의 모든 프로젝트 이름과 설명을 바꿀 수 있습니다. 토큰은 에이전트마다 하나씩 따로 발급하고 공유하지 마세요.
- **토큰 취급**: 로그·`repr`·오류·도구 결과에 토큰을 넣지 않고, 응답에 토큰이 섞여 오면 가립니다. macOS·Linux에서는 권한이 열린 토큰 파일을 거부하고(Windows는 검사 없음), 공백·제어문자가 섞인 토큰은 어디서나 거부합니다.
- **토큰이 갈 수 있는 곳**: API 호스트는 `echo-api.slash.pro`만 허용합니다. 루프백을 포함한 그 밖의 호스트는 `SLASH_PRO_ALLOW_CUSTOM_API=<그 호스트>`(개발·자체 호스팅 전용)가 필요하고, 토큰은 `SLASH_PRO_TOKEN` 값으로만 받으며 토큰 파일은 읽지 않습니다. 남는 위험: 커스텀 호스트를 지정한 프로젝트 설정이 셸에 export한 `SLASH_PRO_TOKEN`을 참조할 수는 있으니, 토큰은 export보다 토큰 파일로 두고 프로젝트 MCP 설정은 확인한 뒤 신뢰하세요. 설정 오류 메시지에는 `scheme://host`만 나옵니다.
- **전송**: HTTPS만 허용하고 TLS 검증은 항상 켜 둡니다. 프록시·CA 환경변수(`HTTPS_PROXY`·`SSL_CERT_FILE` 등)는 **일부러 무시**합니다(`trust_env=False`). 리다이렉트는 따라가지 않고, 연결 10초·읽기 30초 타임아웃과 요청 전체 60초, 도구 호출 전체 120초 기한을 둡니다. 응답은 압축 없이 받고(압축 응답은 본문을 읽기 전에 거부) 10 MB로 제한합니다.
- **입력 검증**: 경로에 들어가는 id·티켓 키는 도구 인자로 받은 것이든 서버가 돌려준 것이든 모두 검증하고 퍼센트 인코딩합니다. 그래서 `../`나 `?x=` 같은 경로·쿼리를 끼워 넣을 수 없습니다.
- **프롬프트 인젝션**: 워크스페이스의 글(티켓·회의·전사·위키·PRD·인계·질문·제목·멤버 이름·서버 경고)이 들어가는 도구 결과(쓰기 확인 문구 포함)와 서버 오류 메시지는 `<untrusted-workspace-content-XXXXXXXX source="…">` 블록으로 감쌉니다. XXXXXXXX는 호출마다 바뀌는 무작위 값이라 내용 안에서 닫는 태그를 위조할 수 없습니다. id·키·상태만 돌려주는 결과(`add_note`·`add_handoff`·`add_evidence`·`update_note`·`update_ticket_status`·`mark_deployed`·삭제 도구)는 감싸지 않되, 서버가 준 id는 UUID나 단순 id 문자열일 때만 그대로 보이고 아니면 감쌉니다. 경로에 쓸 수 없는 서버 id는 값을 보이지 않고 오류로 알립니다. 전송 오류 내용도 감싸고, 서버 오류 메시지는 2,000자·필드 오류 20개까지, 도구 결과는 200,000자까지로 자릅니다. 자세한 내용은 [README.md › Security model](https://github.com/sigcrew/slash-pro-mcp/blob/main/README.md#security-model). 위험을 완전히 없애지는 못하므로, 에이전트가 쓴 내용은 사람이 확인하세요.
- **워크스페이스 규칙**: 시작할 때 규칙 페이지를 읽어 instructions에 넣되 3초 안에 못 읽으면 규칙 없이 시작합니다(클라이언트 연결을 붙잡지 않음, stderr에 경고). 규칙 페이지는 모든 에이전트가 읽기 때문에, 페이지 제목과 본문을 함께 고정 안내문 뒤의 블록 안에 넣습니다. 안내문은 이 규칙이 티켓·노트 처리 관례일 뿐이며 셸 명령 실행·소프트웨어 설치·자격 증명이나 파일 읽기·전송·다른 호스트 접속은 절대 허용하지 않는다고 못박습니다. 이 서버는 그 페이지에 대한 에이전트 쓰기를 거부합니다: 보호 대상 쓰기(위키 쓰기, 제품 전략 문서, 마크다운 PRD, AI 문서 업데이트 수락·되돌리기·거절) 직전마다 규칙 페이지를 다시 조회해 정규화한 UUID로 비교하므로 시작 뒤에 지정된 페이지도 막히고, 조회에 실패하면 쓰기를 거부합니다. `SLASH_PRO_WORKSPACE_RULES=0`이면 본문만 넣지 않고 보호는 유지합니다.
- **클라이언트 쪽 제한은 안전망일 뿐 보안 경계가 아닙니다**: 이 클라이언트는 잘 동작하는 에이전트가 실수로 사람의 작업을 망치지 않도록 몇 가지 제한을 둡니다(예: 자기가 만든 노트·근거만 고치거나 지우기, 사람 항목·판정이 있는 체크리스트 항목 유지, canceled 거부). 권한은 Slash Pro API가 토큰 스코프로 판단합니다. 토큰 스코프는 좁게 주고, 믿을 수 없는 내용을 읽는 에이전트에게는 쓰기 스코프를 주지 말고, 믿을 수 없는 에이전트에게 토큰 파일이나 환경 변수에 닿는 셸 권한을 주지 마세요.
- **텔레메트리 없음**: 네트워크 요청은 설정한 API 주소로만 갑니다.
- **릴리스**: 태그에서 GitHub Actions가 PyPI Trusted Publishing과 빌드 출처 증명으로 배포하고, 태그 커밋이 `main`에 있는지 확인합니다. 관리자는 `pypi` 환경에 필수 리뷰어를 두고 `v*` 태그를 보호하세요.

취약점 제보는 [SECURITY.md](https://github.com/sigcrew/slash-pro-mcp/blob/main/SECURITY.md)를 참고하세요.

## 라이선스

[MIT](https://github.com/sigcrew/slash-pro-mcp/blob/main/LICENSE) © SIG Inc.
