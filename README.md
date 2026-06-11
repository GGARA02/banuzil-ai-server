<a href="https://club-project-one.vercel.app/" target="_blank">
<img width="1000" height="600" alt="Image" src="https://github.com/user-attachments/assets/cd6cf260-4eb0-42d7-9021-4d86bc4b567f" />
</a>

<br/>
<br/>

# 1. Getting Started (시작하기)
```
```
[바느질 서비스 링크](https://banuzil.netlify.app/)

<br/>
<br/>

# 2. Project Overview (프로젝트 개요)
- 프로젝트 이름: 바느질
- 프로젝트 설명: 연인 관계 중재 서비스

<br/>
<br/>

# 3. Team Members (팀원 및 팀 소개)
| 박서연 | 성준서 | 손효리 | 황병부 |
|:------:|:------:|:------:|:------:|
| <img src="https://github.com/user-attachments/assets/c1c2b1e3-656d-4712-98ab-a15e91efa2da" alt="박서연" width="150"> | <img src="https://github.com/user-attachments/assets/78ec4937-81bb-4637-975d-631eb3c4601e" alt="성준서" width="150"> | <img src="https://github.com/user-attachments/assets/78ce1062-80a0-4edb-bf6b-5efac9dd992e" alt="손효리" width="150"> | <img src="https://github.com/user-attachments/assets/beea8c64-19de-4d91-955f-ed24b813a638" alt="황병부" width="150"> |
| FE | BE | FE | AI |
| [GitHub](https://github.com/seoyeon435) | [GitHub](https://github.com/junse02) | [GitHub](https://github.com/dogdevelophoyri) | [GitHub](https://github.com/GGARA02) |

<br/>
<br/>

# 4. Key Features (주요 기능)

👤 유저 관리 및 통계
```
•JWT 기반의 안전한 회원가입 및 로그인
•마이페이지 통계: 내가 참여한 총 갈등 횟수, 성공적인 합의율, 월별 갈등 발생 추이 분석 제공.

```
💬 턴제 갈등 중재 시스템 (Mediation Session)
```
•동시성 제어: 양측(남/녀)이 모두 발화를 제출해야만 다음 단계로 넘어가는 'Wait for Both' 패턴 적용. 트랜잭션 분리를 통해 중복 제출 및 데이터 충돌 방지.
•EFT 단계 관리: 대기중(WAITING) -> 진행중(IN_PROGRESS) -> 종료(COMPLETED)의 상태 머신 관리.

```
🔄 AI 사이클 정의 (Cycle Explore & Define)
```
•일반 대화 중 AI가 악순환 패턴을 감지하면 사이클 탐색 모드로 전환.
•데이터 캐싱: 양측이 동일한 탐색 질문을 받을 수 있도록 DB에 질문 캐싱 처리.
•브릿지 메시지: AI가 사이클을 정의한 후, 프론트엔드 화면에 끊김 없이 상담사 말풍선이 렌더링되도록 브릿지 메시지를 DB에 자동 삽입(Insert).

```

📊 결과 보고서 및 피드백 (Report & Feedback) 
```
•상담 종료 조건(Progress 90 이상) 달성 시, 또는 원할 때 언제든 AI에게 보고서 생성 요청 가능.
•세션 종료 후, 서비스에 대한 별점(1~5점) 및 후기 제출 기능 제공 (중복 방지 처리 완료).
```


# 5. Technology Stack (기술 스택)
## 5.1 Language
|  |  |
|-----------------|-----------------|
| HTML5    |<img src="https://github.com/user-attachments/assets/2e122e74-a28b-4ce7-aff6-382959216d31" alt="HTML5" width="100">| 
| CSS3    |   <img src="https://github.com/user-attachments/assets/c531b03d-55a3-40bf-9195-9ff8c4688f13" alt="CSS3" width="100">|
| Javascript    |  <img src="https://github.com/user-attachments/assets/4a7d7074-8c71-48b4-8652-7431477669d1" alt="Javascript" width="100"> | 
| JAVA 21 | <img src = "https://github.com/user-attachments/assets/9f921738-37a7-42c7-83c4-965047b07bf2" alt = "Java" width = "100"> |

<br/>

## 5.2 Frontend
|  |  |  |
|-----------------|-----------------|-----------------|
| React    |  <img src="https://github.com/user-attachments/assets/e3b49dbb-981b-4804-acf9-012c854a2fd2" alt="React" width="100"> | 18.3.1    |
| TypeScript    |  <img src="https://github.com/user-attachments/assets/6602612c-afec-4589-b7f1-6ab35ac0ea2d" alt="StyledComponents" width="100">|    |


<br/>

## 5.3 Backend
|  |  |  |
|-----------------|-----------------|-----------------|
| Spring Boot    |  <img width="130" height="100" alt="SPB" src="https://github.com/user-attachments/assets/474e5c7d-406a-4c14-a18c-a2cae48aafce" />    | 4.0.5    |
| SUPABASE    |  <img width="130" height="100" alt="Image" src="https://github.com/user-attachments/assets/6511ec24-c4b3-4536-894c-b18758ae0210" />  |  |

<br/>

## 5.4 Cooperation
|  |  |
|-----------------|-----------------|
| Git    |  <img src="https://github.com/user-attachments/assets/483abc38-ed4d-487c-b43a-3963b33430e6" alt="git" width="100">    |
| Swagger   |  <img src="https://github.com/user-attachments/assets/9b460426-9a58-4606-bbb5-a891b380fd15" alt="swagger ui" width="140">    |
| Notion    |  <img src="https://github.com/user-attachments/assets/34141eb9-deca-416a-a83f-ff9543cc2f9a" alt="Notion" width="100">    |

<br/>

# 6. Project Structure (프로젝트 구조)
```
src/main/java/com/mediator/cider/
├── global/                           # 전역 설정 및 공통 로직
│   ├── config/                       # SecurityConfig, CorsConfig 등
│   ├── auth/                         # JwtAuthenticationFilter 등 보안 필터
│   ├── common/                       # BaseTimeEntity 등 공통 엔티티
│   └── JwtProvider.java              # JWT 발급 및 검증 유틸
│
├── domain/                           # 핵심 비즈니스 도메인
│   ├── user/                         # [회원 도메인]
│   │   ├── controller/               # 회원가입, 로그인, 마이페이지 API
│   │   ├── service/                  # 회원 통계(합의율 등) 계산 로직
│   │   └── entity/                   # User 엔티티
│   │
│   ├── mediation/                    # [갈등 중재(Sewing) 핵심 도메인]
│   │   ├── controller/               # 방 생성, 라운드 제출, 사이클 API
│   │   ├── service/                  # 턴제 로직, 상태 검증, AiServerClient 연동
│   │   ├── entity/                   # MediationSession, MediationRecord, Report
│   │   └── dto/                      # 프론트 통신용 DTO 및 AI 서버 통신용 DTO
│   │
│   ├── feedback/                     # [피드백 도메인]
│   │   ├── controller/               # 별점 및 후기 제출 API
│   │   └── entity/                   # Feedback 엔티티
│   │
│   └── attachment/                   # [첨부파일/애착유형 도메인]
```
<br/>
<br/>

# 7. Development Workflow (개발 워크플로우)
## 브랜치 전략 (Branch Strategy)
우리의 브랜치 전략은 Git Flow를 기반으로 하며, 다음과 같은 브랜치를 사용합니다.

- Main Branch
  - 배포 가능한 상태의 코드를 유지합니다.
  - 모든 배포는 이 브랜치에서 이루어집니다.

- Dev
  - 개발 브랜치로, 팀원들은 Dev 브랜치에서 항상 최신 코드를 받아와 작업합니다.
    
- type/기능설명/{name} Branch
  - 팀원 각자의 개발 브랜치입니다.
  - 모든 기능 개발은 이 브랜치에서 이루어집니다.

<br/>
<br/>

# 8. Coding Convention
## 문장 종료
```
// 세미콜론(;)
console.log("Hello World!");
```

<br/>


## 명명 규칙
    // state
    const [isLoading, setIsLoading] = useState(false);
    const [isLoggedIn, setIsLoggedIn] = useState(false);
    const [errorMessage, setErrorMessage] = useState('');
    const [currentUser, setCurrentUser] = useState(null);

    // 배열 - 복수형 이름 사용
    const datas = [];

    // 정규표현식: 'r'로 시작
    const rName = /.*/;

    // 이벤트 핸들러: 'on'으로 시작
    const onClick = () => {};
    const onChange = () => {};

    // 반환 값이 불린인 경우: 'is'로 시작
    const isLoading = false;

    // Fetch함수: method(get, post, put, del)로 시작
    const getEnginList = () => {...}
    

<br/>

## 블록 구문
```
// 한 줄짜리 블록일 경우라도 {}를 생략하지 않고, 명확히 줄 바꿈 하여 사용한다
// good
if(true){
  return 'hello'
}

// bad
if(true) return 'hello'
```

<br/>

## 함수
```
함수는 함수 표현식을 사용하며, 화살표 함수를 사용한다.
// Good
const fnName = () => {};

// Bad
function fnName() {};
```

<br/>

## 태그 네이밍
Styled-component태그 생성 시 아래 네이밍 규칙을 준수하여 의미 전달을 명확하게 한다.<br/>
태그명이 길어지더라도 의미 전달의 명확성에 목적을 두어 작성한다.<br/>
전체 영역: Container<br/>
영역의 묶음: {Name}Area<br/>
의미없는 태그: <><br/>
```
<Container>
  <ContentsArea>
    <Contents>...</Contents>
    <Contents>...</Contents>
  </ContentsArea>
</Container>
```

<br/>

## 폴더 네이밍
카멜 케이스를 기본으로 하며, 컴포넌트 폴더일 경우에만 파스칼 케이스로 사용한다.
```
// 카멜 케이스
camelCase
// 파스칼 케이스
PascalCase
```

<br/>

## 파일 네이밍
```
컴포넌트일 경우만 .jsx 확장자를 사용한다. (그 외에는 .js)
customHook을 사용하는 경우 : use + 함수명
```

<br/>
<br/>

# 9. 커밋 컨벤션
## 기본 구조
```
type : subject

body 
```

<br/>

## type 종류
```
feat : 새로운 기능 추가
fix : 버그 수정
docs : 문서 수정
style : 코드 포맷팅, 세미콜론 누락, 코드 변경이 없는 경우
refactor : 코드 리펙토링
test : 테스트 코드, 리펙토링 테스트 코드 추가
chore : 빌드 업무 수정, 패키지 매니저 수정
```

<br/>

## 커밋 이모지
```
== 코드 관련
📝	코드 작성
🔥	코드 제거
🔨	코드 리팩토링
💄	UI / style 변경

== 문서&파일
📰	새 파일 생성
🔥	파일 제거
📚	문서 작성

== 버그
🐛	버그 리포트
🚑	버그를 고칠 때

== 기타
🐎	성능 향상
✨	새로운 기능 구현
💡	새로운 아이디어
🚀	배포
```

<br/>
