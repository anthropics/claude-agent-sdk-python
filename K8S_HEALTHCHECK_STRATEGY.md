# Kubernetes 헬스체크 전략 for Claude Agent SDK Python

## 프로젝트 특성 분석

### 1. 애플리케이션 타입
- **Python SDK 라이브러리** (배포 가능한 서비스가 아님)
- HTTP 엔드포인트를 제공하지 않음
- Node.js Claude Code CLI를 subprocess로 실행하는 래퍼
- 비동기 I/O 기반 (anyio)

### 2. 실행 환경 요구사항
- Python 3.10+
- Node.js 런타임
- Claude Code CLI (`@anthropic-ai/claude-code`)
- `ANTHROPIC_API_KEY` 환경 변수

### 3. K8s 배포 시나리오
이 SDK를 사용하는 애플리케이션의 일반적인 배포 유형:
1. **웹 API 서버** (FastAPI, Flask 등)
2. **백그라운드 워커** (Celery, RQ 등)
3. **작업 큐 프로세서** (비동기 작업 처리)
4. **이벤트 드리븐 서비스** (메시지 큐 컨슈머)

---

## 현실적이고 비용 효율적인 헬스체크 전략

### 전략 1: 경량 HTTP 헬스체크 엔드포인트 (권장)

#### 적용 대상
- 웹 API 서버
- HTTP 엔드포인트를 추가할 수 있는 모든 애플리케이션

#### 구현 방법

**FastAPI 예시:**

```python
from fastapi import FastAPI, Response
from claude_agent_sdk import query
import asyncio
import os

app = FastAPI()

# 단순 liveness 체크 (프로세스가 살아있는지만 확인)
@app.get("/healthz")
async def liveness():
    """프로세스 생존 여부만 확인 (빠르고 리소스 소모 없음)"""
    return {"status": "ok"}

# 상세 readiness 체크 (실제 동작 가능 여부 확인)
@app.get("/readyz")
async def readiness():
    """
    실제 동작 가능 여부 확인:
    1. 필수 환경 변수 존재
    2. Node.js 및 Claude Code CLI 설치 여부
    """
    checks = {
        "api_key_configured": bool(os.getenv("ANTHROPIC_API_KEY")),
        "node_available": await check_node_installed(),
        "claude_cli_available": await check_claude_cli_installed(),
    }

    all_passed = all(checks.values())
    status_code = 200 if all_passed else 503

    return Response(
        content={"status": "ready" if all_passed else "not_ready", "checks": checks},
        status_code=status_code
    )

async def check_node_installed():
    """Node.js 설치 확인"""
    proc = await asyncio.create_subprocess_exec(
        "node", "--version",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE
    )
    await proc.communicate()
    return proc.returncode == 0

async def check_claude_cli_installed():
    """Claude Code CLI 설치 확인"""
    proc = await asyncio.create_subprocess_exec(
        "npx", "@anthropic-ai/claude-code", "--version",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE
    )
    await proc.communicate()
    return proc.returncode == 0
```

**Kubernetes 매니페스트:**

```yaml
apiVersion: v1
kind: Pod
metadata:
  name: claude-agent-app
spec:
  containers:
  - name: app
    image: your-app:latest
    ports:
    - containerPort: 8000

    # Liveness Probe: 프로세스 응답성 확인 (가볍게)
    livenessProbe:
      httpGet:
        path: /healthz
        port: 8000
      initialDelaySeconds: 10      # 시작 후 10초 대기
      periodSeconds: 10             # 10초마다 체크
      timeoutSeconds: 2             # 2초 타임아웃
      failureThreshold: 3           # 3번 실패 시 재시작
      successThreshold: 1

    # Readiness Probe: 실제 트래픽 받을 준비 확인
    readinessProbe:
      httpGet:
        path: /readyz
        port: 8000
      initialDelaySeconds: 15       # Claude CLI 초기화 시간 고려
      periodSeconds: 10
      timeoutSeconds: 3
      failureThreshold: 3
      successThreshold: 1

    # Startup Probe: 초기 시작 시간이 긴 경우 (선택사항)
    startupProbe:
      httpGet:
        path: /healthz
        port: 8000
      initialDelaySeconds: 0
      periodSeconds: 5
      timeoutSeconds: 2
      failureThreshold: 30          # 최대 150초 (5초 × 30번)
      successThreshold: 1

    resources:
      requests:
        cpu: "100m"                 # 최소 리소스
        memory: "256Mi"
      limits:
        cpu: "1000m"                # 최대 리소스
        memory: "1Gi"               # Node.js subprocess 고려

    env:
    - name: ANTHROPIC_API_KEY
      valueFrom:
        secretKeyRef:
          name: anthropic-secret
          key: api-key
```

#### 비용 및 리소스 효율성
- **CPU**: ~1-2ms per check (헬스체크 엔드포인트는 매우 가벼움)
- **메모리**: 추가 메모리 사용 없음
- **네트워크**: 로컬 호스트 연결 (외부 네트워크 비용 없음)
- **API 비용**: Anthropic API 호출 없음 (환경 변수와 바이너리 존재만 확인)

---

### 전략 2: exec 기반 헬스체크 (HTTP 엔드포인트 없는 경우)

#### 적용 대상
- 백그라운드 워커
- 작업 큐 프로세서
- HTTP 서버가 없는 서비스

#### 구현 방법

**헬스체크 스크립트 (healthcheck.py):**

```python
#!/usr/bin/env python3
"""
경량 헬스체크 스크립트
실제 Claude SDK를 호출하지 않고 프로세스 상태만 확인
"""
import sys
import os
import subprocess

def check_python_process():
    """메인 Python 프로세스가 실행 중인지 확인"""
    # 프로세스 ID 파일 확인 (애플리케이션에서 생성)
    pid_file = "/tmp/app.pid"
    if not os.path.exists(pid_file):
        return False

    try:
        with open(pid_file) as f:
            pid = int(f.read().strip())
        # 프로세스가 실행 중인지 확인
        os.kill(pid, 0)  # signal 0은 프로세스 존재만 확인
        return True
    except (OSError, ValueError):
        return False

def check_environment():
    """필수 환경 변수 확인"""
    return bool(os.getenv("ANTHROPIC_API_KEY"))

def main():
    checks = {
        "process": check_python_process(),
        "environment": check_environment(),
    }

    if all(checks.values()):
        print("OK")
        sys.exit(0)
    else:
        print(f"FAILED: {checks}")
        sys.exit(1)

if __name__ == "__main__":
    main()
```

**메인 애플리케이션에서 PID 파일 생성:**

```python
# worker.py
import os
import signal
import asyncio
from claude_agent_sdk import ClaudeSDKClient

def write_pid_file():
    """PID 파일 생성"""
    with open("/tmp/app.pid", "w") as f:
        f.write(str(os.getpid()))

def cleanup_pid_file():
    """PID 파일 삭제"""
    try:
        os.remove("/tmp/app.pid")
    except OSError:
        pass

async def main():
    # 시작 시 PID 기록
    write_pid_file()

    # 종료 시그널 핸들러
    signal.signal(signal.SIGTERM, lambda s, f: cleanup_pid_file())
    signal.signal(signal.SIGINT, lambda s, f: cleanup_pid_file())

    try:
        # 실제 작업 수행
        async with ClaudeSDKClient() as client:
            while True:
                # 작업 수행
                await asyncio.sleep(1)
    finally:
        cleanup_pid_file()

if __name__ == "__main__":
    asyncio.run(main())
```

**Kubernetes 매니페스트:**

```yaml
apiVersion: v1
kind: Pod
metadata:
  name: claude-worker
spec:
  containers:
  - name: worker
    image: your-worker:latest

    # Liveness Probe: exec 기반
    livenessProbe:
      exec:
        command:
        - python3
        - /app/healthcheck.py
      initialDelaySeconds: 10
      periodSeconds: 30             # exec는 비용이 더 높으므로 간격 증가
      timeoutSeconds: 5
      failureThreshold: 3

    # Readiness Probe: 동일
    readinessProbe:
      exec:
        command:
        - python3
        - /app/healthcheck.py
      initialDelaySeconds: 15
      periodSeconds: 30
      timeoutSeconds: 5
      failureThreshold: 3

    resources:
      requests:
        cpu: "100m"
        memory: "256Mi"
      limits:
        cpu: "1000m"
        memory: "1Gi"
```

#### 비용 및 리소스 효율성
- **CPU**: ~5-10ms per check (프로세스 생성 오버헤드)
- **메모리**: ~10-20MB 일시적 증가 (Python 인터프리터 실행)
- **주의**: HTTP보다 무거우므로 check 간격을 늘림 (30초 권장)

---

### 전략 3: TCP 소켓 헬스체크 (가장 경량)

#### 적용 대상
- 극도로 리소스가 제한된 환경
- 최소한의 오버헤드만 허용되는 경우

#### 구현 방법

**애플리케이션에 TCP 리스너 추가:**

```python
import asyncio
import signal

async def health_check_server(port=9090):
    """
    TCP 연결만 받고 즉시 종료하는 최소 헬스체크 서버
    데이터 교환 없음, 연결 가능 여부만 확인
    """
    async def handle_client(reader, writer):
        writer.close()
        await writer.wait_closed()

    server = await asyncio.start_server(handle_client, "0.0.0.0", port)

    async with server:
        await server.serve_forever()

async def main():
    # 헬스체크 서버를 백그라운드 태스크로 실행
    health_task = asyncio.create_task(health_check_server(9090))

    # 실제 애플리케이션 로직
    from claude_agent_sdk import ClaudeSDKClient
    async with ClaudeSDKClient() as client:
        # 작업 수행
        pass

if __name__ == "__main__":
    asyncio.run(main())
```

**Kubernetes 매니페스트:**

```yaml
apiVersion: v1
kind: Pod
metadata:
  name: claude-worker
spec:
  containers:
  - name: worker
    image: your-worker:latest
    ports:
    - containerPort: 9090
      name: health

    # Liveness Probe: TCP 소켓
    livenessProbe:
      tcpSocket:
        port: health
      initialDelaySeconds: 5
      periodSeconds: 10
      timeoutSeconds: 1
      failureThreshold: 3

    # Readiness Probe: TCP 소켓
    readinessProbe:
      tcpSocket:
        port: health
      initialDelaySeconds: 5
      periodSeconds: 10
      timeoutSeconds: 1
      failureThreshold: 3
```

#### 비용 및 리소스 효율성
- **CPU**: <1ms per check (가장 가벼움)
- **메모리**: ~1KB per connection (거의 무시 가능)
- **네트워크**: TCP handshake만 발생
- **장점**: 가장 빠르고 가벼움
- **단점**: 실제 애플리케이션 상태를 확인하지 못함 (프로세스 존재만 확인)

---

## 권장 전략 선택 가이드

### 시나리오별 추천

| 애플리케이션 타입 | 추천 전략 | 이유 |
|------------------|----------|------|
| **Web API (FastAPI, Flask)** | 전략 1 (HTTP) | 기존 HTTP 서버 활용, 구현 간단, 상세한 체크 가능 |
| **백그라운드 워커** | 전략 2 (exec) | HTTP 서버 불필요, 프로세스 상태 직접 확인 |
| **메시지 큐 컨슈머** | 전략 2 (exec) | 큐 처리 상태 체크 가능 |
| **극도로 경량 필요** | 전략 3 (TCP) | 오버헤드 최소화, 단순 생존 확인 |
| **멀티 컨테이너 Pod** | 전략 1 (HTTP) | 사이드카 헬스체크 서버 패턴 활용 |

### 리소스 비교표

| 전략 | CPU/check | Memory | 구현 복잡도 | 상태 확인 정확도 |
|------|-----------|--------|------------|----------------|
| HTTP | 1-2ms | ~0 | 낮음 | 높음 |
| exec | 5-10ms | 10-20MB | 중간 | 중간 |
| TCP | <1ms | ~1KB | 낮음 | 낮음 |

---

## 실전 최적화 팁

### 1. 초기 지연 시간 설정

Claude Agent SDK는 다음 초기화 시간이 필요합니다:
- Python 프로세스 시작: ~2-3초
- Node.js CLI subprocess 시작: ~3-5초
- 첫 API 호출: ~1-2초

**권장 설정:**
```yaml
initialDelaySeconds: 15    # 안전한 시작 대기
```

### 2. 실패 임계값 설정

네트워크 지터와 일시적 오류를 허용:
```yaml
failureThreshold: 3        # 3번 연속 실패 시 조치
periodSeconds: 10          # 10초마다 체크
# = 총 30초 후 재시작
```

### 3. 리소스 제한 고려

Claude Agent SDK는 Node.js subprocess를 실행하므로:
```yaml
resources:
  limits:
    memory: "1Gi"          # Node.js는 ~300-500MB 사용 가능
    cpu: "1000m"
  requests:
    memory: "256Mi"        # 기본 Python 프로세스
    cpu: "100m"
```

### 4. Startup Probe 활용 (K8s 1.18+)

초기 시작이 느린 경우:
```yaml
startupProbe:
  httpGet:
    path: /healthz
    port: 8000
  failureThreshold: 30     # 최대 150초 허용
  periodSeconds: 5
```

이후 liveness/readiness가 활성화됩니다.

---

## 비용 절감 전략

### 1. 헬스체크 간격 최적화

**안티패턴:**
```yaml
periodSeconds: 1          # 너무 자주 체크 (불필요한 CPU 사용)
```

**권장:**
```yaml
periodSeconds: 10         # HTTP의 경우
periodSeconds: 30         # exec의 경우
```

### 2. 불필요한 API 호출 방지

**절대 하지 말 것:**
```python
@app.get("/healthz")
async def health():
    # ❌ 헬스체크에서 실제 API 호출하지 말 것!
    async for msg in query("test"):
        pass
    return {"status": "ok"}
```

**올바른 방법:**
```python
@app.get("/healthz")
async def health():
    # ✅ 환경 및 프로세스 상태만 확인
    return {"status": "ok"}
```

### 3. 캐싱 활용

의존성 체크 결과 캐싱:
```python
from functools import lru_cache
from time import time

@lru_cache(maxsize=1)
def cached_check_with_ttl(ttl_hash):
    """TTL 기반 캐싱 (60초)"""
    return {
        "node": check_node_installed(),
        "cli": check_claude_cli_installed(),
    }

def get_ttl_hash(seconds=60):
    return round(time() / seconds)

@app.get("/readyz")
async def readiness():
    checks = cached_check_with_ttl(get_ttl_hash(60))
    return checks
```

---

## 모니터링 및 알림

### Prometheus 메트릭 추가 (선택사항)

```python
from prometheus_client import Counter, Histogram, generate_latest

# 메트릭 정의
health_check_total = Counter('health_check_total', 'Total health checks')
health_check_failures = Counter('health_check_failures', 'Failed health checks')
query_duration = Histogram('claude_query_duration_seconds', 'Query duration')

@app.get("/metrics")
async def metrics():
    return Response(content=generate_latest(), media_type="text/plain")

@app.get("/healthz")
async def health():
    health_check_total.inc()
    try:
        # 체크 로직
        return {"status": "ok"}
    except Exception:
        health_check_failures.inc()
        raise
```

---

## 결론

### 최종 권장사항

**대부분의 경우: 전략 1 (HTTP 헬스체크)**
- 구현 간단
- 리소스 효율적
- 상세한 상태 확인 가능
- K8s 네이티브 지원

**핵심 원칙:**
1. ✅ **절대 실제 API 호출하지 않기** (비용 발생)
2. ✅ **환경 변수 및 바이너리 존재만 확인**
3. ✅ **적절한 간격 설정** (10-30초)
4. ✅ **리소스 제한 설정** (Node.js 메모리 고려)
5. ✅ **실패 허용** (네트워크 지터 고려)

이 전략을 따르면 **추가 비용 없이** 안정적인 K8s 배포가 가능합니다.
