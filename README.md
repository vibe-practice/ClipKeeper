# ClipKeeper

윈도우용 클립보드 기록 프로그램. 복사한 텍스트, 이미지, 파일을 모두 기록해요.

| 단축키 | 동작 |
|---|---|
| Alt+V | 기록 창 열기/닫기 (Enter로 원래 창에 바로 붙여넣기) |
| Alt+Shift+V | 숨긴 항목 열기/닫기 |
| Ctrl+H (목록에서) | 선택 항목 숨기기 / 숨김 해제 |
| Ctrl+F | 검색 |
| Delete | 선택 항목 삭제 |
| Esc | 창 닫기 (트레이에서 계속 기록) |

기록 위치: `%APPDATA%\ClipKeeper`

## 처음 설치

1. Python 설치 (설치할 때 "Add python.exe to PATH" 체크)
2. 이 폴더에서 `install.bat` 더블클릭 → 바탕화면에 `ClipKeeper.exe`가 생겨요

## 자동 업데이트

프로그램이 켜진 뒤 10초 후, 그리고 6시간마다 GitHub Releases를 확인해요.
새 버전이 있으면 내려받아 두었다가, 창을 안 쓰고 있을 때 몇 초 만에 바꿔 끼우고 다시 실행해요.
트레이 메뉴의 "업데이트 확인"으로 바로 확인할 수도 있어요.

### 새 버전 내보내는 법

1. `clipkeeper.py`의 `APP_VERSION`을 올려요 (예: `1.2.0` → `1.3.0`)
2. 커밋하고 같은 이름의 태그를 올려요

   ```
   git commit -am "v1.3.0"
   git tag v1.3.0
   git push && git push --tags
   ```

3. GitHub Actions가 윈도우에서 exe를 만들어 Releases에 올려요 (5분 정도)
4. 각자 PC의 ClipKeeper가 알아서 업데이트해요
