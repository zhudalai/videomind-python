# VideoMind セキュリティ設計

> JWT 認証 + API Key AES-GCM 暗号化 + レートリミット + 監査ログ + CORS/CSRF 防護
> 主要参考：Ragent `infra-auth/` + DOVideo-AI セキュリティモジュール + OWASP ASVS L2 標準

---

## 1. セキュリティアーキテクチャ概要

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                     セキュリティ境界 (Security Perimeter)                    │
│  ┌─────────────────────────────────────────────────────────────────────┐   │
│  │                        API Gateway / Ingress                        │   │
│  │  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────────────┐  │   │
│  │  │  TLS     │  │  CORS    │  │  Rate    │  │  WAF Rules       │  │   │
│  │  │  Term    │  │  Policy  │  │  Limit   │  │  (SQLi/XSS/Path) │  │   │
│  │  └──────────┘  └──────────┘  └──────────┘  └──────────────────┘  │   │
│  └────────────────────────────────┬────────────────────────────────────┘   │
│                                   │                                       │
│  ┌────────────────────────────────┴────────────────────────────────────┐   │
│  │                      Authentication Layer                           │   │
│  │  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐  ┌───────────┐  │   │
│  │  │ JWT Access  │  │ JWT Refresh │  │ API Key     │  │  Session  │  │   │
│  │  │  Token      │  │  Token      │  │  (AES-GCM)  │  │  Cookie   │  │   │
│  │  └─────────────┘  └─────────────┘  └─────────────┘  └───────────┘  │   │
│  └────────────────────────────────┬────────────────────────────────────┘   │
│                                   │                                       │
│  ┌────────────────────────────────┴────────────────────────────────────┐   │
│  │                      Authorization Layer (RBAC + ABAC)              │   │
│  │  Roles: admin, user, analyst, viewer                                 │   │
│  │  Permissions: video:read, video:write, analysis:create, admin:*     │   │
│  └────────────────────────────────┬────────────────────────────────────┘   │
│                                   │                                       │
│  ┌────────────────────────────────┴────────────────────────────────────┐   │
│  │                      Audit & Monitoring                             │   │
│  │  Structured JSON Logs → Loki/ELK → Alerting                         │   │
│  └─────────────────────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────────────────────┘
```

---

## 2. 認証システム

### 2.1 JWT デュアルトークン機構

```python
class JWTConfig:
    # アクセストークン：短期、メモリ上のみ
    ACCESS_TOKEN_EXPIRE_MINUTES = 15
    # リフレッシュトークン：長期、HttpOnly Cookie に保存
    REFRESH_TOKEN_EXPIRE_DAYS = 30
    # 署名アルゴリズム
    ALGORITHM = "RS256"  # 非対称、秘密鍵で署名、公開鍵で検証
    # 鍵ローテーション
    KEY_ROTATION_DAYS = 90


class TokenPayload(BaseModel):
    sub: str                    # user_id
    email: str
    roles: list[str]            # ["user", "analyst"]
    permissions: list[str]      # ["video:read", "analysis:create"]
    type: Literal["access", "refresh"]
    jti: str                    # JWT ID、失効に使用
    iat: int
    exp: int
    device_id: str | None = None  # デバイスフィンガープリント


class JWTService:
    def __init__(self, private_key: str, public_key: str, config: JWTConfig):
        self.private_key = private_key
        self.public_key = public_key
        self.config = config
        self.redis: Redis  # トークン失効リストに使用
    
    def create_access_token(self, user: User, device_id: str | None = None) -> str:
        payload = TokenPayload(
            sub=str(user.id),
            email=user.email,
            roles=[r.name for r in user.roles],
            permissions=self._get_permissions(user.roles),
            type="access",
            jti=uuid4().hex,
            iat=int(time.time()),
            exp=int(time.time()) + self.config.ACCESS_TOKEN_EXPIRE_MINUTES * 60,
            device_id=device_id
        )
        return jwt.encode(payload.model_dump(), self.private_key, algorithm=self.config.ALGORITHM)
    
    def create_refresh_token(self, user: User, device_id: str | None = None) -> str:
        payload = TokenPayload(
            sub=str(user.id),
            email=user.email,
            roles=[r.name for r in user.roles],
            permissions=[],
            type="refresh",
            jti=uuid4().hex,
            iat=int(time.time()),
            exp=int(time.time()) + self.config.REFRESH_TOKEN_EXPIRE_DAYS * 86400,
            device_id=device_id
        )
        return jwt.encode(payload.model_dump(), self.private_key, algorithm=self.config.ALGORITHM)
    
    def verify_token(self, token: str, token_type: Literal["access", "refresh"]) -> TokenPayload:
        try:
            payload = jwt.decode(
                token,
                self.public_key,
                algorithms=[self.config.ALGORITHM],
                audience="videomind",
                issuer="videomind-auth"
            )
        except jwt.ExpiredSignatureError:
            raise TokenExpiredError()
        except jwt.InvalidTokenError:
            raise InvalidTokenError()
        
        # タイプをチェック
        if payload.get("type") != token_type:
            raise InvalidTokenError("Token type mismatch")
        
        # 失効リストをチェック
        if self._is_revoked(payload["jti"]):
            raise TokenRevokedError()
        
        return TokenPayload(**payload)
    
    def revoke_token(self, jti: str, ttl: int):
        """失効リストに追加（Redis SET、TTL はトークンの有効期限と同じ）"""
        self.redis.setex(f"revoked:{jti}", ttl, "1")
    
    def _is_revoked(self, jti: str) -> bool:
        return self.redis.exists(f"revoked:{jti}")
    
    async def refresh_access_token(self, refresh_token: str) -> tuple[str, str]:
        """リフレッシュトークンローテーション：refresh を検証 → 新しい access + 新しい refresh を発行 → 旧 refresh を失効"""
        payload = self.verify_token(refresh_token, "refresh")
        
        # 旧 refresh を失効
        self.revoke_token(payload.jti, self.config.REFRESH_TOKEN_EXPIRE_DAYS * 86400)
        
        # ユーザーの最新権限を取得
        user = await self.user_repo.get(UUID(payload.sub))
        if not user or not user.is_active:
            raise UserInactiveError()
        
        device_id = payload.device_id
        new_access = self.create_access_token(user, device_id)
        new_refresh = self.create_refresh_token(user, device_id)
        
        return new_access, new_refresh
```

### 2.2 API Key 管理（AES-GCM 暗号化保存）

```python
class APIKeyManager:
    """
    API Key 設計：
    - プレフィックス識別：vk_live_ / vk_test_ / vk_dev_
    - フォーマット：{prefix}_{random32}.{checksum8}
    - 保存：ハッシュ（argon2id）+ 暗号化済み平文（AES-GCM、下4桁表示用）のみを保存
    - 権限：ロール権限セットに紐付け、IP ホワイトリストと呼び出し頻度制限をサポート
    """
    
    PREFIX_MAP = {
        "live": "vk_live_",
        "test": "vk_test_",
        "dev": "vk_dev_"
    }
    
    def __init__(self, master_key: bytes, redis: Redis):
        # master_key: 32 bytes、KMS または環境変数からロード
        self.cipher = AESGCM(master_key)
        self.redis = redis
    
    def generate_key(self, user_id: UUID, name: str, env: str = "live", 
                     permissions: list[str] | None = None, 
                     ip_whitelist: list[str] | None = None,
                     rate_limit: int = 1000) -> APIKey:
        # ランダム部分を生成
        random_part = secrets.token_urlsafe(24)  # 32 bytes -> 32 chars
        prefix = self.PREFIX_MAP[env]
        raw_key = f"{prefix}{random_part}"
        
        # チェックサム（先頭 8 桁の SHA256）
        checksum = hashlib.sha256(raw_key.encode()).hexdigest()[:8]
        full_key = f"{raw_key}.{checksum}"
        
        # ハッシュを保存（検証用）
        key_hash = hash_password(raw_key)  # argon2id
        
        # 平文を暗号化して保存（管理画面で下 4 桁を表示するために使用）
        nonce = secrets.token_bytes(12)
        encrypted = self.cipher.encrypt(nonce, raw_key.encode(), None)
        encrypted_b64 = base64.b64encode(nonce + encrypted).decode()
        
        api_key = APIKey(
            id=uuid4(),
            user_id=user_id,
            name=name,
            key_hash=key_hash,
            encrypted_key=encrypted_b64,
            key_prefix=raw_key[:12],  # vk_live_abc123
            key_suffix=raw_key[-4:],   # 下 4 桁を平文で表示
            permissions=permissions or [],
            ip_whitelist=ip_whitelist or [],
            rate_limit=rate_limit,
            env=env,
            created_at=datetime.utcnow(),
            last_used_at=None,
            expires_at=None,
            is_active=True
        )
        
        await self.repo.insert(api_key)
        return api_key
    
    async def verify_key(self, provided_key: str, client_ip: str) -> APIKey | None:
        # 1. フォーマット検証
        if not self._validate_format(provided_key):
            return None
        
        raw_key, checksum = provided_key.rsplit(".", 1)
        if hashlib.sha256(raw_key.encode()).hexdigest()[:8] != checksum:
            return None
        
        # 2. プレフィックス検索
        prefix = raw_key[:12]
        candidates = await self.repo.find_by_prefix(prefix)
        
        for candidate in candidates:
            if not candidate.is_active:
                continue
            if candidate.expires_at and candidate.expires_at < datetime.utcnow():
                continue
            
            # IP ホワイトリスト
            if candidate.ip_whitelist and client_ip not in candidate.ip_whitelist:
                continue
            
            # ハッシュを検証
            if verify_password(raw_key, candidate.key_hash):
                # 最終使用時刻を更新
                await self.repo.update_last_used(candidate.id)
                return candidate
        
        return None
    
    def _validate_format(self, key: str) -> bool:
        # vk_{env}_{32chars}.{8chars}
        pattern = r"^vk_(live|test|dev)_[A-Za-z0-9_-]{32}\.[a-f0-9]{8}$"
        return bool(re.match(pattern, key))
    
    def decrypt_for_display(self, encrypted_b64: str) -> str:
        """管理画面で完全な key を表示するための復号（再確認が必要）"""
        data = base64.b64decode(encrypted_b64)
        nonce, ciphertext = data[:12], data[12:]
        return self.cipher.decrypt(nonce, ciphertext, None).decode()
```

---

## 3. 認可システム (RBAC + ABAC)

```python
class Permission(StrEnum):
    # 動画
    VIDEO_READ = "video:read"
    VIDEO_WRITE = "video:write"
    VIDEO_DELETE = "video:delete"
    VIDEO_INGEST = "video:ingest"
    
    # 分析
    ANALYSIS_CREATE = "analysis:create"
    ANALYSIS_READ = "analysis:read"
    ANALYSIS_DELETE = "analysis:delete"
    
    # RAG
    RAG_QUERY = "rag:query"
    RAG_MANAGE_KB = "rag:manage_kb"
    
    # システム
    ADMIN_USERS = "admin:users"
    ADMIN_SYSTEM = "admin:system"
    ADMIN_AUDIT = "admin:audit"


ROLE_PERMISSIONS: dict[str, list[Permission]] = {
    "viewer": [Permission.VIDEO_READ, Permission.ANALYSIS_READ, Permission.RAG_QUERY],
    "user": [Permission.VIDEO_READ, Permission.VIDEO_WRITE, Permission.VIDEO_INGEST,
             Permission.ANALYSIS_CREATE, Permission.ANALYSIS_READ, Permission.RAG_QUERY],
    "analyst": [Permission.VIDEO_READ, Permission.VIDEO_WRITE, Permission.VIDEO_INGEST,
                Permission.ANALYSIS_CREATE, Permission.ANALYSIS_READ, Permission.ANALYSIS_DELETE,
                Permission.RAG_QUERY, Permission.RAG_MANAGE_KB],
    "admin": [p for p in Permission]  # すべての権限
}


class AuthorizationService:
    def __init__(self, user_repo: UserRepo, casbin_enforcer: Enforcer):
        self.user_repo = user_repo
        self.enforcer = casbin_enforcer  # Casbin は ABAC の複雑なポリシーに使用
    
    async def check_permission(self, user_id: UUID, permission: Permission, 
                               resource: Resource | None = None) -> bool:
        user = await self.user_repo.get(user_id)
        if not user or not user.is_active:
            return False
        
        # 1. RBAC 基本チェック
        user_perms = set()
        for role in user.roles:
            user_perms.update(ROLE_PERMISSIONS.get(role.name, []))
        
        if permission not in user_perms:
            return False
        
        # 2. ABAC リソース単位チェック（例：自分がアップロードした動画のみ操作可能）
        if resource:
            return await self._check_abac(user, permission, resource)
        
        return True
    
    async def _check_abac(self, user: User, permission: Permission, resource: Resource) -> bool:
        # リソース所有者のチェック
        if resource.owner_id == user.id:
            return True
        
        # 組織/チーム共有のチェック
        if resource.team_id and user.team_id == resource.team_id:
            # チーム内権限：viewer は読み取り専用、user は書き込み可能
            if permission in (Permission.VIDEO_READ, Permission.ANALYSIS_READ):
                return True
            if permission in (Permission.VIDEO_WRITE, Permission.ANALYSIS_CREATE) and any(r.name in ("user", "analyst") for r in user.roles):
                return True
        
        # 公開リソース
        if resource.visibility == "public" and permission in (Permission.VIDEO_READ, Permission.RAG_QUERY):
            return True
        
        return False
```

---

## 4. レートリミット

```python
class RateLimiter:
    """
    多層レートリミット：
    1. グローバル：IP 単位（DDoS 対策）
    2. ユーザー単位：user_id（悪用防止）
    3. API Key 単位：key_id（クォータ制御）
    4. エンドポイント単位：機密性の高いインターフェースには追加制限
    
    アルゴリズム：スライディングウィンドウ + トークンバケット（Redis Lua で原子性を保証）
    """
    
    def __init__(self, redis: Redis):
        self.redis = redis
    
    async def check_limit(self, 
                          key: str, 
                          limit: int, 
                          window_seconds: int,
                          burst: int | None = None) -> RateLimitResult:
        """
        スライディングウィンドウカウンター：
        - 各リクエストのタイムスタンプを記録（Sorted Set）
        - ウィンドウ外の古い記録をクリーンアップ
        - 現在ウィンドウ内のカウントを集計
        """
        now = time.time()
        window_start = now - window_seconds
        
        lua = """
        local key = KEYS[1]
        local now = tonumber(ARGV[1])
        local window_start = tonumber(ARGV[2])
        local limit = tonumber(ARGV[3])
        local burst = tonumber(ARGV[4])
        
        -- 期限切れをクリーンアップ
        redis.call('ZREMRANGEBYSCORE', key, '-inf', window_start)
        
        -- 現在のカウント
        local count = redis.call('ZCARD', key)
        
        -- トークンバケット：バーストをチェック
        local tokens_key = key .. ':tokens'
        local tokens = tonumber(redis.call('GET', tokens_key) or burst)
        if tokens > burst then tokens = burst end
        
        local allowed = 0
        local retry_after = 0
        
        if count < limit and tokens > 0 then
            -- 許可
            redis.call('ZADD', key, now, now .. ':' .. math.random())
            redis.call('EXPIRE', key, math.ceil(ARGV[5]))
            redis.call('DECR', tokens_key)
            allowed = 1
        else
            -- 拒否
            local oldest = redis.call('ZRANGE', key, 0, 0, 'WITHSCORES')
            if #oldest > 0 then
                retry_after = math.ceil(tonumber(oldest[2]) + ARGV[5] - now)
            end
        end
        
        return {allowed, count + 1, limit, retry_after}
        """
        
        result = await self.redis.eval(lua, 1, key, now, window_start, limit, burst or limit, window_seconds)
        allowed, current, limit, retry_after = result
        
        return RateLimitResult(
            allowed=bool(allowed),
            current=current,
            limit=limit,
            remaining=max(0, limit - current),
            retry_after=retry_after if not allowed else 0,
            reset_at=int(now + window_seconds)
        )


# FastAPI 依存性注入
async def rate_limit_dependency(
    request: Request,
    limiter: RateLimiter = Depends(get_rate_limiter)
):
    # IP 単位のグローバルレートリミット
    ip = request.client.host
    global_result = await limiter.check_limit(f"global:ip:{ip}", 1000, 60)  # 1000/min
    
    # ユーザー単位のレートリミット
    user_id = getattr(request.state, "user_id", None)
    if user_id:
        user_result = await limiter.check_limit(f"user:{user_id}", 200, 60)  # 200/min
        if not user_result.allowed:
            raise RateLimitExceededError(user_result)
    
    # API Key 単位のレートリミット
    api_key_id = getattr(request.state, "api_key_id", None)
    if api_key_id:
        key_result = await limiter.check_limit(f"apikey:{api_key_id}", 1000, 60)
        if not key_result.allowed:
            raise RateLimitExceededError(key_result)
    
    # レスポンスヘッダー
    request.state.rate_limit_headers = {
        "X-RateLimit-Limit": str(global_result.limit),
        "X-RateLimit-Remaining": str(global_result.remaining),
        "X-RateLimit-Reset": str(global_result.reset_at)
    }
```

---

## 5. 監査ログ

```python
class AuditLogger:
    """構造化監査ログ：Loki/ELK に書き込み、リアルタイムアラートをサポート"""
    
    def __init__(self, log_client: LogClient):
        self.log_client = log_client
    
    async def log(self, event: AuditEvent):
        # 統一フィールド
        log_entry = {
            "timestamp": datetime.utcnow().isoformat() + "Z",
            "level": "INFO",
            "service": "videomind-api",
            "trace_id": event.trace_id or get_current_trace_id(),
            "span_id": get_current_span_id(),
            
            # イベント識別
            "event_type": event.event_type,      # AUTH_LOGIN, VIDEO_UPLOAD, ANALYSIS_START, etc.
            "event_category": event.category,    # AUTH, DATA, ADMIN, SECURITY
            
            # 主体
            "user_id": str(event.user_id) if event.user_id else None,
            "api_key_id": str(event.api_key_id) if event.api_key_id else None,
            "ip": event.ip,
            "user_agent": event.user_agent,
            "device_id": event.device_id,
            
            # 対象
            "resource_type": event.resource_type,  # video, analysis, user, api_key
            "resource_id": str(event.resource_id) if event.resource_id else None,
            
            # アクション
            "action": event.action,                # create, read, update, delete, execute
            "result": event.result,                # success, failure, denied
            "error_code": event.error_code,
            "error_message": event.error_message,
            
            # コンテキスト
            "metadata": event.metadata,            # 柔軟な拡張フィールド
            
            # コンプライアンス
            "compliance_tags": event.compliance_tags  # ["GDPR", "PIPL", "SOC2"]
        }
        
        # 機密フィールドのマスキング
        log_entry = self._sanitize(log_entry)
        
        await self.log_client.send(log_entry)
    
    def _sanitize(self, entry: dict) -> dict:
        sensitive_keys = {"password", "token", "secret", "key", "authorization", "cookie"}
        def sanitize_obj(obj):
            if isinstance(obj, dict):
                return {k: "***" if k.lower() in sensitive_keys else sanitize_obj(v) for k, v in obj.items()}
            elif isinstance(obj, list):
                return [sanitize_obj(v) for v in obj]
            return obj
        return sanitize_obj(entry)


# 監査イベントタイプ列挙
class AuditEventType(StrEnum):
    # 認証
    AUTH_LOGIN = "auth.login"
    AUTH_LOGOUT = "auth.logout"
    AUTH_LOGIN_FAILED = "auth.login_failed"
    AUTH_TOKEN_REFRESH = "auth.token_refresh"
    AUTH_PASSWORD_CHANGE = "auth.password_change"
    AUTH_MFA_ENABLE = "auth.mfa_enable"
    
    # API Key
    APIKEY_CREATE = "apikey.create"
    APIKEY_REVOKE = "apikey.revoke"
    APIKEY_USE = "apikey.use"
    
    # 動画
    VIDEO_UPLOAD = "video.upload"
    VIDEO_DOWNLOAD = "video.download"
    VIDEO_DELETE = "video.delete"
    VIDEO_INGEST_START = "video.ingest_start"
    VIDEO_INGEST_COMPLETE = "video.ingest_complete"
    
    # 分析
    ANALYSIS_CREATE = "analysis.create"
    ANALYSIS_EXECUTE = "analysis.execute"
    ANALYSIS_COMPLETE = "analysis.complete"
    
    # RAG
    RAG_QUERY = "rag.query"
    RAG_KB_CREATE = "rag.kb_create"
    
    # 管理
    ADMIN_USER_CREATE = "admin.user_create"
    ADMIN_USER_DELETE = "admin.user_delete"
    ADMIN_ROLE_ASSIGN = "admin.role_assign"
    ADMIN_CONFIG_CHANGE = "admin.config_change"
    
    # セキュリティ
    SECURITY_RATE_LIMIT_EXCEEDED = "security.rate_limit_exceeded"
    SECURITY_SUSPICIOUS_ACTIVITY = "security.suspicious_activity"
    SECURITY_PERMISSION_DENIED = "security.permission_denied"
```

---

## 6. CORS / CSRF / セキュリティヘッダー

```python
# FastAPI ミドルウェア設定
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.trustedhost import TrustedHostMiddleware

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS,  # 本番ドメインのみ、"*" なし
    allow_credentials=True,                # Cookie 認証が必要な場合
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type", "X-Request-ID", "X-CSRF-Token"],
    expose_headers=["X-RateLimit-Limit", "X-RateLimit-Remaining", "X-RateLimit-Reset"],
    max_age=600
)

app.add_middleware(
    TrustedHostMiddleware,
    allowed_hosts=settings.ALLOWED_HOSTS  # ["api.videomind.com", "localhost"]
)


# セキュリティレスポンスヘッダーミドルウェア
@app.middleware("http")
async def security_headers(request: Request, call_next):
    response = await call_next(request)
    
    # HSTS
    response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains; preload"
    
    # CSP
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; "
        "script-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net; "
        "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
        "font-src 'self' https://fonts.gstatic.com; "
        "img-src 'self' data: https:; "
        "connect-src 'self' wss: https:; "
        "frame-ancestors 'none'; "
        "base-uri 'self'; "
        "form-action 'self'"
    )
    
    # その他のセキュリティヘッダー
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["X-XSS-Protection"] = "1; mode=block"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
    
    # サーバー情報を除去
    response.headers.pop("Server", None)
    
    return response


# CSRF 防護（Cookie 認証による状態変更エンドポイント向け）
class CSRFMiddleware:
    def __init__(self, secret_key: str):
        self.secret_key = secret_key
    
    async def __call__(self, request: Request, call_next):
        if request.method in ("POST", "PUT", "PATCH", "DELETE"):
            # Cookie 認証のリクエストのみ CSRF をチェック
            if request.cookies.get("refresh_token"):
                csrf_token = request.headers.get("X-CSRF-Token")
                if not csrf_token or not self._validate_csrf(csrf_token, request):
                    return JSONResponse(
                        status_code=403,
                        content={"detail": "CSRF token missing or invalid"}
                    )
        
        response = await call_next(request)
        
        # 新しい CSRF Token を発行（ダブルサブミットパターン）
        if request.method == "GET" and not request.cookies.get("csrf_token"):
            new_token = self._generate_csrf_token(request)
            response.set_cookie(
                "csrf_token", new_token,
                httponly=True, secure=True, samesite="strict", max_age=3600
            )
            response.headers["X-CSRF-Token"] = new_token
        
        return response
```

---

## 7. データ暗号化

```python
class DataEncryption:
    """フィールドレベル暗号化：PII、API Key 平文、Webhook Secret など"""
    
    def __init__(self, master_key: bytes):
        # KMS から取得、または環境変数から導出
        self.field_cipher = AESGCM(master_key)
    
    def encrypt_field(self, plaintext: str) -> str:
        nonce = secrets.token_bytes(12)
        ciphertext = self.field_cipher.encrypt(nonce, plaintext.encode(), None)
        return base64.b64encode(nonce + ciphertext).decode()
    
    def decrypt_field(self, encrypted_b64: str) -> str:
        data = base64.b64decode(encrypted_b64)
        nonce, ciphertext = data[:12], data[12:]
        return self.field_cipher.decrypt(nonce, ciphertext, None).decode()


# SQLAlchemy タイプデコレーター
class EncryptedString(TypeDecorator):
    impl = String
    cache_ok = True
    
    def __init__(self, *args, encryption: DataEncryption, **kwargs):
        super().__init__(*args, **kwargs)
        self.encryption = encryption
    
    def process_bind_param(self, value: str | None, dialect) -> str | None:
        if value is None:
            return None
        return self.encryption.encrypt_field(value)
    
    def process_result_value(self, value: str | None, dialect) -> str | None:
        if value is None:
            return None
        return self.encryption.decrypt_field(value)


# 使用例
class User(Base):
    __tablename__ = "user"
    
    id = Column(UUID, primary_key=True)
    email = Column(String(255), unique=True, index=True)
    # 電話番号を暗号化して保存
    phone = Column(EncryptedString(255, encryption=encryption), nullable=True)
    # 実名を暗号化して保存
    real_name = Column(EncryptedString(100, encryption=encryption), nullable=True)
```

---

## 8. セキュリティ設定チェックリスト

```yaml
# config/security.yaml
jwt:
  algorithm: "RS256"
  access_token_expire_minutes: 15
  refresh_token_expire_days: 30
  key_rotation_days: 90
  issuer: "videomind-auth"
  audience: "videomind"

api_key:
  prefix_map:
    live: "vk_live_"
    test: "vk_test_"
    dev: "vk_dev_"
  hash_algorithm: "argon2id"
  encryption: "AES-256-GCM"

rate_limit:
  global:
    ip_per_minute: 1000
  user:
    per_minute: 200
  api_key:
    per_minute: 1000
  endpoints:
    "/api/v1/auth/login": 5/minute
    "/api/v1/auth/register": 3/minute
    "/api/v1/videos/ingest": 10/minute

cors:
  allow_origins:
    - "https://videomind.com"
    - "https://app.videomind.com"
  allow_credentials: true

security_headers:
  hsts_max_age: 31536000
  csp: "default-src 'self'; script-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net; ..."
  frame_options: "DENY"
  content_type_options: "nosniff"

audit:
  enabled: true
  log_level: "INFO"
  compliance_tags: ["PIPL", "GDPR"]
  retention_days: 365

encryption:
  field_encryption_enabled: true
  kms_provider: "local"  # または aws_kms, hashicorp_vault
  key_rotation_enabled: true
```

---

## 9. イベント検知と対応

```python
class SecurityMonitor:
    """リアルタイムセキュリティイベント検知"""
    
    def __init__(self, redis: Redis, alert_webhook: str):
        self.redis = redis
        self.alert_webhook = alert_webhook
    
    async def check_anomalies(self, event: AuditEvent):
        checks = [
            self._check_brute_force(event),
            self._check_credential_stuffing(event),
            self._check_impossible_travel(event),
            self._check_api_abuse(event),
            self._check_data_exfiltration(event)
        ]
        
        for check in checks:
            if await check:
                await self._trigger_alert(event, check.__name__)
    
    async def _check_brute_force(self, event: AuditEvent) -> bool:
        if event.event_type != AuditEventType.AUTH_LOGIN_FAILED:
            return False
        
        key = f"sec:bruteforce:ip:{event.ip}"
        count = await self.redis.incr(key)
        if count == 1:
            await self.redis.expire(key, 900)  # 15 分ウィンドウ
        
        return count >= 10  # 15 分以内に 10 回失敗
    
    async def _check_impossible_travel(self, event: AuditEvent) -> bool:
        if not event.user_id or event.event_type != AuditEventType.AUTH_LOGIN:
            return False
        
        last_login = await self.redis.get(f"sec:last_login:{event.user_id}")
        if not last_login:
            return False
        
        last_data = json.loads(last_login)
        # 物理距離/時間が妥当かを計算（簡易版）
        # 実運用では MaxMind GeoIP を統合可能
        return False
    
    async def _trigger_alert(self, event: AuditEvent, rule_name: str):
        alert = {
            "alert_type": "security_anomaly",
            "rule": rule_name,
            "severity": "high",
            "event": event.model_dump(),
            "timestamp": datetime.utcnow().isoformat()
        }
        await self.http_client.post(self.alert_webhook, json=alert)
        logger.warning(f"Security alert triggered: {rule_name}", extra=alert)
```

---

> **関連ドキュメント**：[ARCHITECTURE_JP.md](ARCHITECTURE_JP.md) · [MODEL-GATEWAY_JP.md](MODEL-GATEWAY_JP.md) · [TASK-ORCHESTRATION_JP.md](TASK-ORCHESTRATION_JP.md) · [DEPLOYMENT_JP.md](DEPLOYMENT_JP.md) · [OBSERVABILITY_JP.md](OBSERVABILITY_JP.md)